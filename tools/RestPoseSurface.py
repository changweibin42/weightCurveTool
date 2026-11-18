# -*- coding: utf-8 -*-
"""RestPoseSurface —— mesh 的 rest pose 表面 + 8D MDS 嵌入 + 双空间

职责：
  · 复制一份 triangulate mesh（不带 deformer）作为 rest pose 表面
  · 算 N×N 测地距离 → MDS 嵌入 8 维 → 缓存到 triangulate 的 attribute
  · 对外提供：
      copy_data / iter_curve / distances    —— 表面几何操作
      solve_curve_weights / solve_loft_weights / solve_point_weights  —— 8D 权重
      local_to_display / display_to_local   —— rest pose ↔ 当前 pose

缓存失效：
  · embeddings 缓存在 triangulate 节点的 wcs_embedding attribute
  · 指纹包含缓存算法版本、Rest Pose 顶点坐标和三角面连接关系
  · 缓存缺少指纹、形状错误或包含非有限数值时重新计算
"""
import hashlib
import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om

from ..Logger import log
from .kd_tree_copy import KDTreeCopy
from .mds import get_mds_embeddings_fast
from .geodesic import get_fast_distance_matrix

try:
    from .NativeProjection import compute_if_enabled as _native_project_points
except ImportError:
    _native_project_points = None


class RestPoseSurface(object):
    TRIANGULATE_SUFFIX = "_WCS_Triangulate"
    EMBEDDING_ATTR = "wcs_embedding"

    def __init__(self, mesh_name):
        self.mesh_name = mesh_name
        self.triangulate_name = None
        self.core = None
        self.embeddings = None
        self.display_kd = None
        self.display_fingerprint = None
        self._build()

    # ==================== 构建 ====================

    def _build(self):
        self._ensure_triangulate()
        vertices, faces = self._get_triangulate_geo()
        fingerprint = self._embedding_fingerprint(vertices, faces)
        log.info("[RestPoseSurface Cache] mesh={}, verts={}, faces={}, fingerprint={}".format(
            self.mesh_name, len(vertices), len(faces), fingerprint[:12]))

        embeddings = self._read_cached_embedding(fingerprint, len(vertices))
        if embeddings is None:
            log.info("[RestPoseSurface Cache] MISS -> rebuild embedding")
            log.info("[RestPoseSurface] {}: 计算测地距离 ({} verts)...".format(
                self.mesh_name, len(vertices)))
            distances = get_fast_distance_matrix(vertices, faces)
            log.info("[RestPoseSurface] MDS 嵌入到 8 维...")
            embeddings = get_mds_embeddings_fast(distances, n_components=8)
            embeddings = np.asarray(embeddings, dtype=np.float64)
            if embeddings.shape != (len(vertices), 8) or not np.all(np.isfinite(embeddings)):
                raise RuntimeError(
                    "MDS 嵌入结果无效: shape={}, expected=({}, 8)".format(
                        embeddings.shape, len(vertices)))
            self._write_cached_embedding(embeddings, fingerprint)

        self.embeddings = embeddings
        self.core = KDTreeCopy(vertices, faces)
        log.info("[RestPoseSurface] 构建完成: {} verts, {} faces".format(
            len(vertices), len(faces)))

    def _ensure_triangulate(self):
        name = self.mesh_name + self.TRIANGULATE_SUFFIX
        if cmds.objExists(name):
            self.triangulate_name = name
            return
        dup = cmds.duplicate(self.mesh_name, n=name)[0]
        for trs in "trs":
            for xyz in "xyz":
                cmds.setAttr(dup + "." + trs + xyz, l=0)
        cmds.setAttr(dup + ".v", l=0)
        cmds.setAttr(dup + ".v", 0)
        if cmds.listRelatives(dup, p=1):
            cmds.parent(dup, w=1)
        cmds.select(dup)
        cmds.makeIdentity(dup, apply=1, t=1, r=1, s=1)
        cmds.ResetTransformations()
        cmds.polyTriangulate(dup, ch=0)
        self.triangulate_name = name

    def _get_triangulate_geo(self):
        sel = om.MSelectionList()
        sel.add(self.triangulate_name)
        fn = om.MFnMesh(sel.getDagPath(0))
        vertices = np.array(cmds.xform(
            self.triangulate_name + ".vtx[*]", q=True, ws=True, t=1)).reshape(-1, 3)
        _, face_connects = fn.getVertices()
        faces = np.array(face_connects).reshape(-1, 3)
        return vertices, faces

    @staticmethod
    def _embedding_fingerprint(vertices, faces):
        """为 Rest Pose 几何生成稳定指纹；不依赖当前变形 Pose。"""
        vertices = np.ascontiguousarray(np.asarray(vertices, dtype=np.float64))
        faces = np.ascontiguousarray(np.asarray(faces, dtype=np.int64))
        digest = hashlib.blake2b(digest_size=20)
        # 修改嵌入算法或缓存语义时递增版本，主动淘汰旧缓存。
        digest.update(b"RestPoseSurface.embedding.v2")
        digest.update(np.asarray(vertices.shape, dtype=np.int64).tobytes())
        digest.update(vertices.tobytes())
        digest.update(np.asarray(faces.shape, dtype=np.int64).tobytes())
        digest.update(faces.tobytes())
        return digest.hexdigest()

    def _read_cached_embedding(self, fingerprint, vertex_count):
        fingerprint_attr = self.triangulate_name + ".wcs_embedding_fingerprint"
        if not cmds.attributeQuery(self.EMBEDDING_ATTR, node=self.triangulate_name, exists=True):
            log.info("[RestPoseSurface Cache] MISS: embedding attribute missing")
            return None
        if not cmds.attributeQuery("wcs_embedding_fingerprint",
                                   node=self.triangulate_name, exists=True):
            # 旧版本缓存没有几何指纹，不能证明与当前 Rest Pose 一致。
            log.info("[RestPoseSurface Cache] MISS: legacy cache has no fingerprint")
            return None
        try:
            cached_fingerprint = cmds.getAttr(fingerprint_attr)
            if cached_fingerprint != fingerprint:
                cached_short = str(cached_fingerprint)[:12] if cached_fingerprint else "<empty>"
                log.info("[RestPoseSurface Cache] MISS: fingerprint mismatch cached={} current={}".format(
                    cached_short, fingerprint[:12]))
                return None
            raw = cmds.getAttr(self.triangulate_name + "." + self.EMBEDDING_ATTR)
            arr = np.asarray(raw, dtype=np.float64).reshape(-1, 8)
            if arr.shape != (vertex_count, 8):
                log.info("[RestPoseSurface Cache] INVALID: shape={} expected=({}, 8)".format(
                    arr.shape, vertex_count))
                return None
            if not np.all(np.isfinite(arr)):
                log.info("[RestPoseSurface Cache] INVALID: embedding contains NaN/Inf")
                return None
            log.info("[RestPoseSurface Cache] HIT: embedding shape={}, fingerprint={}".format(
                arr.shape, fingerprint[:12]))
            return arr
        except Exception as exc:
            log.warn("[RestPoseSurface Cache] INVALID: cache read failed: {}".format(exc))
            return None

    def _write_cached_embedding(self, embeddings, fingerprint):
        embeddings = np.asarray(embeddings, dtype=np.float64)
        if embeddings.ndim != 2 or embeddings.shape[1] != 8:
            raise ValueError("嵌入缓存必须为 N x 8 数组")
        if not np.all(np.isfinite(embeddings)):
            raise ValueError("嵌入缓存包含 NaN 或 Inf")

        if not cmds.attributeQuery(self.EMBEDDING_ATTR, node=self.triangulate_name, exists=True):
            cmds.addAttr(self.triangulate_name, ln=self.EMBEDDING_ATTR, dt="doubleArray")
        if not cmds.attributeQuery("wcs_embedding_fingerprint",
                                   node=self.triangulate_name, exists=True):
            cmds.addAttr(self.triangulate_name, ln="wcs_embedding_fingerprint", dt="string")

        # 先写数据，最后写指纹；中途失败时旧指纹不会错误地认证新数据。
        cmds.setAttr(self.triangulate_name + "." + self.EMBEDDING_ATTR,
                     embeddings.reshape(-1).tolist(), type="doubleArray")
        cmds.setAttr(self.triangulate_name + ".wcs_embedding_fingerprint",
                     fingerprint, type="string")
        log.info("[RestPoseSurface Cache] WRITE: embedding shape={}, fingerprint={}".format(
            embeddings.shape, fingerprint[:12]))

    # ==================== 双空间 ====================

    def _update_display(self):
        try:
            shapes = cmds.listRelatives(self.mesh_name, shapes=True, type='mesh')
            if shapes:
                cmds.dgdirty(shapes[0])
        except Exception:
            pass
    
        points = np.array(cmds.xform(
            self.mesh_name + ".vtx[*]", q=True, ws=True, t=1)).reshape(-1, 3)
        fp = (points.shape,
              hashlib.blake2b(np.ascontiguousarray(points).tobytes(), digest_size=16).digest())
        if fp != self.display_fingerprint:
            self.display_kd = KDTreeCopy(points, self.core.faces)
            self.display_fingerprint = fp
        return self.display_kd
    
    def local_to_display(self, points):
        """rest pose → 当前 pose。保留原始点到表面的偏移（含 offset）。"""
        self._update_display()
        pts = np.asarray(points, dtype=float)
        faces, weights = self.core.get_face_weights(pts)
    
        rest_surf = (self.core.vertices[self.core.faces[faces]]
                     * weights[:, :, None]).sum(axis=1)
        offset_vec = pts - rest_surf
    
        display_surf = (self.display_kd.vertices[self.core.faces[faces]]
                        * weights[:, :, None]).sum(axis=1)
        return display_surf + offset_vec
    
    
    def display_to_local(self, points):
        """当前 pose → rest pose。保留原始点到表面的偏移。"""
        self._update_display()
        pts = np.asarray(points, dtype=float)
        faces, weights = self.display_kd.get_face_weights(pts)
    
        display_surf = (self.display_kd.vertices[self.display_kd.faces[faces]]
                        * weights[:, :, None]).sum(axis=1)
        offset_vec = pts - display_surf
    
        rest_surf = (self.core.vertices[self.core.faces[faces]]
                     * weights[:, :, None]).sum(axis=1)
        return rest_surf + offset_vec
        
    def project_to_surface(self, points, use_mesh_api=True):
        """投影到 rest pose 表面。
    
        use_mesh_api=True: 用 MFnMesh.getClosestPoint 精确投影（<1e-5，需要 Maya）
        use_mesh_api=False: 用 copy_data 重心凸组合（~1e-2，不需要 Maya）
    
        ★ 与 copy_data 的区别：
          · copy_data：最近顶点邻域凸组合，把任意点拉到"邻域凸包内"，近似最近点。
            用于在表面采样权重（8D 距离、loft）。
          · project_to_surface：MFnMesh.getClosestPoint，真正的最近点投影。
            用于曲线 EP 贴面、BFS 圈定的边界点。
        """
        arr = np.asarray(points, dtype=float)
        if not use_mesh_api:
            return self.copy_data(arr)
        backend=globals().get('_native_project_points')
        if backend:
            try:
                result=backend(self.triangulate_name,arr)
            except Exception as e:
                print('[WCT Native Projection] Python fallback: {}'.format(e))
                result=None
            if result is not None:return result
        try:
            sel = om.MSelectionList()
            sel.add(self.triangulate_name)
            fn = om.MFnMesh(sel.getDagPath(0))
            out = np.empty_like(arr)
            for i in range(len(arr)):
                p = arr[i]
                c, _ = fn.getClosestPoint(
                    om.MPoint(float(p[0]), float(p[1]), float(p[2])),
                    om.MSpace.kWorld)
                out[i] = [c.x, c.y, c.z]
            return out
        except Exception as e:
            from ..Logger import log
            log.warn("[RestPoseSurface] project_to_surface fallback to copy_data: {}".format(e))
            return self.copy_data(arr)  
            
            
    def get_uvw(self, point, project_first=True):
        """用 rest pose 三角面求 point 的 (ids, u, v, w)。
    
        参数：
            point           rest pose 世界坐标
            project_first   True 时先精确投影到表面再求（推荐）
        返回：
            (ids, u, v, w) 或 None
        """
        p = np.asarray([point], dtype=float)
        if project_first:
            p = self.project_to_surface(p)
        faces, weights = self.core.get_face_weights(p)
        face_id = int(faces[0])
        ids = self.core.faces[face_id].tolist()
        uvw = weights[0]
        return ids, float(uvw[0]), float(uvw[1]), float(uvw[2])
    
    
    def get_rest_position(self, ids, uvw):
        """用 rest pose 顶点 + ids + uvw 插值 rest pose 位置。"""
        v = self.core.vertices
        i0, i1, i2 = ids
        u, vv, w = uvw
        return u * v[i0] + vv * v[i1] + w * v[i2]   
        
        
    def get_joint_rest_position(self, jnt):
        """从 jnt 的 wct_* + rest 顶点读 rest pose 位置。
    
        jnt 的 wct_mesh 必须和 self.mesh_name 一致。
        无 wct_* 或 mesh 不一致 → None。
        """
        if not cmds.objExists(jnt):
            return None
        if not cmds.attributeQuery('wct_id0', node=jnt, exists=True):
            return None
        try:
            mesh_name = cmds.getAttr(f"{jnt}.wct_mesh")
            if mesh_name != self.mesh_name:
                return None
            id0 = int(cmds.getAttr(f"{jnt}.wct_id0"))
            id1 = int(cmds.getAttr(f"{jnt}.wct_id1"))
            id2 = int(cmds.getAttr(f"{jnt}.wct_id2"))
            u = float(cmds.getAttr(f"{jnt}.wct_u"))
            v = float(cmds.getAttr(f"{jnt}.wct_v"))
            w = float(cmds.getAttr(f"{jnt}.wct_w"))
        except Exception:
            return None
        n = self.core.vtx_count
        if min(id0, id1, id2) < 0 or max(id0, id1, id2) >= n:
            return None
        if not np.all(np.isfinite((u, v, w))):
            return None
        return self.get_rest_position((id0, id1, id2), (u, v, w))
                               

    # ==================== 几何能力 ====================

    def copy_data(self, points, data=None):
        return self.core.copy_data(points, data)

    def iter_curve(self, points, close=True, count=2):
        return self.core.iter_curve(points, close=close, count=count)

    def distances(self, points):
        """点到 rest pose 表面的距离（精确投影，非 copy_data 近似）"""
        pts = np.asarray(points, dtype=float)
        on_surface = self.project_to_surface(pts)
        return np.linalg.norm(on_surface - pts, axis=1)

    # ==================== 权重求解（8D） ====================

    def _bezier(self, raw, x):
        from .WeightCalculator import get_weight_array
        return get_weight_array(raw, (0.0, x, 1 - x, 1.0), (0.0, 0.0, 1.0, 1.0))

    def solve_curve_weights(self, ctrl_points, close=True, radius=1.0, x=0.3):
        """单曲线权重：顶点到曲线的 8D 距离 → clip(1 - d/radius) → bezier"""
        curve_points = self.core.iter_curve(ctrl_points, close=close)
        high_curve = self.core.copy_data(curve_points, self.embeddings)
        kd_curve = KDTreeCopy(*_curve_geo(high_curve, close))
        distances = kd_curve.distances(self.embeddings)
        raw = np.clip(1 - distances / radius, 0, 1)
        return self._bezier(raw, x)

    def solve_loft_weights(self, curves, ws, radius=1.0, x=0.3):
        """多环 loft：所有环贴面 + 8D 坐标 + loft 几何 + 重心插值权重"""
        curves = [self.core.iter_curve(c, close=True, count=2) for c in curves]
        high_curves = [self.core.copy_data(c, self.embeddings) for c in curves]
        high_geo = _loft_geo(high_curves, close=True)
        weight_kd = KDTreeCopy(*high_geo)
        curve_count = len(curves)
        cv_count = weight_kd.vtx_count // curve_count
        geo_weights = np.zeros((curve_count, cv_count))
        geo_weights[:, :] = np.asarray(ws, dtype=float)[:, None]
        geo_weights = geo_weights.reshape(-1)[:, None]
        raw = weight_kd.copy_data(self.embeddings, geo_weights)[:, 0]
        return self._bezier(raw, x)

    def solve_point_weights(self, point, radius=1.0, x=0.3):
        high_point = self.core.copy_data(np.asarray(point, dtype=float)[None], self.embeddings)
        distances = np.linalg.norm(self.embeddings - high_point[0], axis=1)
        raw = np.clip(1 - distances / radius, 0, 1)
        return self._bezier(raw, x)


    # ==================== raw 权重（不套 bezier，供 WeightCalculator 用） ====================

    def solve_curve_raw(self, ctrl_points, close=True, radius=1.0):
        """单曲线 raw：clip(1 - 8D距离/radius)，0-1。脊线处≈1，radius 外=0。"""
        curve_points = self.core.iter_curve(ctrl_points, close=close)
        high_curve = self.core.copy_data(curve_points, self.embeddings)
        kd_curve = KDTreeCopy(*_curve_geo(high_curve, close))
        distances = kd_curve.distances(self.embeddings)
        return np.clip(1.0 - distances / radius, 0.0, 1.0)

    def solve_point_raw(self, point, radius=1.0):
        """单点 raw：clip(1 - 8D距离/radius)，0-1。"""
        high_point = self.core.copy_data(
            np.asarray(point, dtype=float)[None], self.embeddings)
        distances = np.linalg.norm(self.embeddings - high_point[0], axis=1)
        return np.clip(1.0 - distances / radius, 0.0, 1.0)

    def solve_loft_raw(self, curves, ws):
        """多环 loft raw：按 ws（每条环的权重值）在 8D 空间 loft 插值。

        raw ∈ [0,1]，语义由 ws 决定：
          · soft: ws=[0,1,0]  → raw = "脊线接近度"（脊线1，侧线0）
          · ik:   ws=[0,0.5,1] → raw = "旧 ik x"（fwd线0，脊线0.5，bwd线1）
        """
        curves = [self.core.iter_curve(c, close=True, count=2) for c in curves]
        high_curves = [self.core.copy_data(c, self.embeddings) for c in curves]
        high_geo = _loft_geo(high_curves, close=True)
        weight_kd = KDTreeCopy(*high_geo)
        curve_count = len(curves)
        cv_count = weight_kd.vtx_count // curve_count
        geo_weights = np.zeros((curve_count, cv_count))
        geo_weights[:, :] = np.asarray(ws, dtype=float)[:, None]
        geo_weights = geo_weights.reshape(-1)[:, None]
        return weight_kd.copy_data(self.embeddings, geo_weights)[:, 0]


# ==================== 内部工具 ====================

def _curve_geo(vertices, close):
    vtx_count = vertices.shape[0]
    ids = list(range(vtx_count))
    if close:
        ids.append(0)
    return vertices, np.array(list(zip(ids, ids[1:])))


def _loft_geo(curves, close=True):
    """把多条曲线 loft 成网格。支持不同长度的输入曲线（re-sample 到统一）。"""
    # ★ re-sample 到最短长度（>=8），保证 reshape 不崩
    n_target = max(8, min(len(c) for c in curves))
    uniform = []
    for c in curves:
        arr = np.asarray(c, dtype=float)
        if len(arr) == n_target:
            uniform.append(arr)
        else:
            from .curve_core import re_curve
            uniform.append(re_curve(arr, count=n_target, close=close))
    vertices = np.concatenate(uniform, axis=0)
    ids = np.arange(len(vertices)).reshape(len(uniform), -1)
    if close:
        ids = np.concatenate([ids, ids[:, [0]]], axis=1)
    faces = []
    for i in range(ids.shape[0] - 1):
        for j in range(ids.shape[1] - 1):
            face = ids[i:i + 2, j:j + 2].reshape(-1)
            face[-1], face[-2] = face[-2], face[-1]
            faces.append(face)
    return vertices, np.array(faces)