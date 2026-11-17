# -*- coding: utf-8 -*-
"""SurfaceSlideBuilder —— 脊线沿表面滑动生成两侧曲线 + fwd/bwd 骨骼"""
import maya.cmds as cmds
import maya.api.OpenMaya as om
import numpy as np

try:
    from scipy.spatial import cKDTree
except ImportError:
    cKDTree = None

from ..Logger import log
from .CurveRefine import refine_points_along_surface, create_periodic_curve
from . import ColorScheme


class SurfaceSlideBuilder(object):
    """把脊线沿 mesh 表面两侧滑动，生成曲线与骨骼

    ★ 所有 mesh 数据走 worldMesh 输出 plug —— 包含 skinCluster / blendShape
      等 deformer 效果（求值后的数据）。不能用 MFnMesh(dagPath)：
      那是 shape 的原始网格数据，模型姿势变化后会拿到旧位置。
    """

    def __init__(self,
                 slide_steps=30,
                 ridge_samples=150,
                 lap_iter=12,
                 lap_weight=0.7,
                 kdtree_min_verts=5000,
                 fwd_joint_color=ColorScheme.FWD_JOINT_COLOR,
                 bwd_joint_color=ColorScheme.BWD_JOINT_COLOR,
                 fwd_curve_color=ColorScheme.FWD_CURVE_COLOR,
                 bwd_curve_color=ColorScheme.BWD_CURVE_COLOR,
                 curve_line_width=5):
        self.slide_steps = slide_steps
        self.ridge_samples = ridge_samples
        self.lap_iter = lap_iter
        self.lap_weight = lap_weight
        self.kdtree_min_verts = kdtree_min_verts

        self.fwd_joint_color = fwd_joint_color
        self.bwd_joint_color = bwd_joint_color
        self.fwd_curve_color = fwd_curve_color
        self.bwd_curve_color = bwd_curve_color

        self.curve_line_width = float(curve_line_width)

        self._mesh_shape_name = None
        self._kdtree = None
        self._kdtree_pts = None
        self._kdtree_norms = None
        self._kdtree_mesh = None
        self._kdtree_fp = None
        self._use_kdtree = False

        self._ridge_eps = None

    # ==================== 对外主入口 ====================

    def build(self, mesh_name, ridge_crv_name, distance, locs=None,
              click_pos=None, verbose=True, make_curves=True, 
              rest_surface=None):
        """生成 fwd / bwd 曲线 + 骨骼。

        make_curves：
            True（默认）—— 用 SurfaceSlideBuilder 内部算法建曲线。
            False       —— 只算 EP + 建骨骼，不建曲线。
                           交给外部（如 DualCurveBuilder）用统一算法建。
        """
        if not cmds.objExists(mesh_name) or not cmds.objExists(ridge_crv_name):
            if verbose:
                log.error("mesh 或脊线不存在")
            return None

        fn = self._get_mesh_fn(mesh_name)
        self._prepare_kdtree(mesh_name, fn, verbose)

        closed = self._curve_is_closed(ridge_crv_name)
        # Each output point inherits the SAME source EP index on both sides.
        # Sampling each side by its own arclength loses this correspondence.
        ridge_pts = self._read_curve_eps(ridge_crv_name)
        if ridge_pts is None:
            log.warn("Cannot read ordered ridge EPs; boundary generation cancelled")
            return None
        tangents = self._ridge_tangents(ridge_pts, closed)

        self._ridge_eps = self._read_curve_eps(ridge_crv_name)
        if verbose and self._ridge_eps is not None:
            log.info(f"脊线 EP 数 = {len(self._ridge_eps)}")

        fwd_side = self._decide_fwd_side(fn, ridge_pts, tangents,
                                        click_pos, verbose)

        ep_pos = self._build_side(mesh_name, fn, ridge_pts, tangents,
                                  distance, side=+1, closed=closed,
                                  rest_surface=rest_surface)
        ep_neg = self._build_side(mesh_name, fn, ridge_pts, tangents,
                                  distance, side=-1, closed=closed,
                                  rest_surface=rest_surface)

        if fwd_side > 0:
            ep_fwd, ep_bwd = ep_pos, ep_neg
        else:
            ep_fwd, ep_bwd = ep_neg, ep_pos

        name_fwd = f"{mesh_name}_fwd_ring_laplacian"
        name_bwd = f"{mesh_name}_bwd_ring_laplacian"

        crv_fwd = None
        crv_bwd = None
        if make_curves:
            crv_fwd = self._make_curve(ep_fwd, name_fwd, closed, self.fwd_curve_color)
            crv_bwd = self._make_curve(ep_bwd, name_bwd, closed, self.bwd_curve_color)

        result = {
            'fwd_curve': crv_fwd,
            'bwd_curve': crv_bwd,
            'ep_fwd': ep_fwd,
            'ep_bwd': ep_bwd,
            'fwd_joints': [],
            'bwd_joints': [],
            'closed': closed,
        }

        if locs:
            n_ep = len(ep_fwd)
            fwd_joints, bwd_joints = self._create_joints(
                locs, ep_fwd, ep_bwd, n_ep, verbose)
            result['fwd_joints'] = fwd_joints
            result['bwd_joints'] = bwd_joints
            result['anchor_ep_indices'] = list(self._last_anchor_indices)

        return result

    def slide_side_from_curve(self, mesh_name, ridge_crv_name, distance, side,
                              verbose=True, rest_surface=None):
        """从任意一条曲线滑出一侧的 EP。

        与 build() 内部的 _build_side 同一算法，但用于"外部提供脊线曲线 +
        距离"的场景。

        参数：
            mesh_name        网格名
            ridge_crv_name   脊线曲线名（作为滑动起点）
            distance         滑动距离
            side             +1 或 -1

        返回：
            new_ep（list of [x, y, z]）或 None
        """
        if not cmds.objExists(mesh_name) or not cmds.objExists(ridge_crv_name):
            return None

        fn = self._get_mesh_fn(mesh_name)
        if fn is None:
            return None
        self._prepare_kdtree(mesh_name, fn, verbose)

        closed = self._curve_is_closed(ridge_crv_name)
        # Each output point inherits the SAME source EP index on both sides.
        # Sampling each side by its own arclength loses this correspondence.
        ridge_pts = self._read_curve_eps(ridge_crv_name)
        if ridge_pts is None:
            log.warn("Cannot read ordered ridge EPs; boundary generation cancelled")
            return None
        tangents = self._ridge_tangents(ridge_pts, closed)

        return self._build_side(mesh_name, fn, ridge_pts, tangents,
                                distance, side=side, closed=closed,
                                rest_surface=rest_surface)

    # ==================== fwd 侧判定 ====================

    def _decide_fwd_side(self, fn, ridge_pts, tangents, click_pos, verbose):
        if click_pos is None:
            if verbose:
                log.info("未提供点击位置，默认 +1 侧为 fwd")
            return +1

        click_pt = np.array(click_pos, dtype=float)
        diffs = ridge_pts - click_pt
        d = np.linalg.norm(diffs, axis=1)
        idx = int(np.argmin(d))

        p = ridge_pts[idx]
        # ★ fn 是 worldMesh 数据，取法线用 kObject
        r = fn.getClosestNormal(
            om.MPoint(float(p[0]), float(p[1]), float(p[2])),
            om.MSpace.kObject)
        nv = r[0] if isinstance(r, tuple) else r
        N = np.array([nv.x, nv.y, nv.z])
        ln = np.linalg.norm(N)
        if ln > 1e-10:
            N = N / ln

        T = tangents[idx]
        S = np.cross(N, T)
        ls = np.linalg.norm(S)
        if ls < 1e-10:
            if verbose:
                log.info("侧向退化，默认 +1 侧为 fwd")
            return +1
        S = S / ls

        v_off = click_pt - ridge_pts[idx]
        proj = float(np.dot(v_off, S))

        fwd_side = +1 if proj >= 0 else -1
        if verbose:
            log.info(f"点击 -> 脊线 idx={idx}，侧向投影={proj:+.4f}，fwd_side={fwd_side:+d}")
        return fwd_side

    # ==================== 读脊线 EP ====================

    def _read_curve_eps(self, crv_name):
        if not cmds.objExists(crv_name):
            return None
        components = cmds.ls(crv_name + ".ep[*]", fl=True) or []
        if len(components) < 2:
            return None
        # xform applies the complete DAG matrix: parent, rotation AND scale.
        # getAttr(ep) plus transform translation alone is not world space.
        points = np.array([cmds.xform(ep, q=True, ws=True, t=True)
                           for ep in components], dtype=float)
        if self._curve_is_closed(crv_name) and len(points) > 2:
            if np.linalg.norm(points[0]-points[-1]) < 1e-8:
                points = points[:-1]
        return points

    # ==================== mesh / KDTree ====================

    def _get_mesh_fn(self, mesh_name):
        """拿**求值后**的 MFnMesh（走 worldMesh 输出 plug）。

        ★ worldMesh 数据包含 skinCluster / blendShape 等所有 deformer 效果。
        ★ 不能用 MFnMesh(dagPath)：那是 shape 的原始网格数据。
        ★ 不缓存句柄：姿势可能随时变化，每次都拿最新。
        """
        if not mesh_name or not cmds.objExists(mesh_name):
            return None
        shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
        if not shapes:
            return None
        self._mesh_shape_name = shapes[0]
        try:
            sel = om.MSelectionList(); sel.add(shapes[0])
            dag = sel.getDagPath(0)
            dep = om.MFnDependencyNode(dag.node())
            plug = dep.findPlug("worldMesh", False).elementByLogicalIndex(0)
            return om.MFnMesh(plug.asMObject())
        except Exception:
            return None

    def _prepare_kdtree(self, mesh_name, fn, verbose=True):
        if fn is None:
            return
        try:
            pts = fn.getPoints(om.MSpace.kObject)   # worldMesh 数据已在世界空间
            pts_arr = np.array([[p.x, p.y, p.z] for p in pts])
        except Exception:
            return
        n = len(pts_arr)

        # ★ 指纹：采样 32 个等距点 + 顶点数。姿势变化 / 拓扑变化都会让指纹变，
        #   触发 KDTree 重建。不这样做的话，姿势变了但指纹没变（或没有指纹），
        #   KDTree 会用旧位置 → 投影到旧表面 → 曲线 W 型。
        if n > 0:
            n_sample = min(32, n)
            idxs = np.linspace(0, n - 1, n_sample).astype(int)
            fp = (n, tuple((round(float(pts_arr[i, 0]), 3),
                            round(float(pts_arr[i, 1]), 3),
                            round(float(pts_arr[i, 2]), 3)) for i in idxs))
        else:
            fp = (0, ())

        if (self._kdtree is not None
                and self._kdtree_mesh == mesh_name
                and self._kdtree_fp == fp):
            return

        self._kdtree_fp = fp
        self._kdtree_mesh = mesh_name

        if cKDTree is None or n < self.kdtree_min_verts:
            self._use_kdtree = False
            self._kdtree = None
            if verbose:
                log.info(f"滑动模式: 精确 API（顶点 {n}）")
            return

        try:
            normals = fn.getVertexNormals(True, om.MSpace.kObject)
        except Exception:
            try:
                normals = fn.getVertexNormals(False, om.MSpace.kObject)
            except Exception:
                normals = fn.getVertexNormals(True, om.MSpace.kWorld)
        norms_arr = np.array([[nv.x, nv.y, nv.z] for nv in normals])
        lens = np.linalg.norm(norms_arr, axis=1, keepdims=True)
        lens[lens < 1e-10] = 1.0
        norms_arr = norms_arr / lens

        self._kdtree = cKDTree(pts_arr)
        self._kdtree_pts = pts_arr
        self._kdtree_norms = norms_arr
        self._use_kdtree = True
        if verbose:
            log.info(f"滑动模式: KDTree（顶点 {n}）")

    def _query_fast(self, x, y, z, k=4):
        """单点查询：返回 (point, normal)"""
        d, idx = self._kdtree.query([x, y, z], k=k)
        if np.isscalar(d):
            d = np.array([d]); idx = np.array([idx])
        w = 1.0 / (d + 1e-6)
        w = w / w.sum()
        point = (self._kdtree_pts[idx] * w[:, None]).sum(axis=0)
        normal = (self._kdtree_norms[idx] * w[:, None]).sum(axis=0)
        ln = np.linalg.norm(normal)
        if ln > 1e-10:
            normal = normal / ln
        return point, normal

    def _project_batch(self, points_arr):
        """★ 批量投影：一次 KDTree 查询处理 N 个点（KDTree 模式）
             无 KDTree 时逐点走 MFnMesh 精确 API"""
        arr = np.asarray(points_arr, dtype=float)
        if arr.size == 0:
            return arr
        if self._use_kdtree:
            k = min(4, len(self._kdtree_pts))
            d, idxs = self._kdtree.query(arr, k=k)
            w = 1.0 / (d + 1e-6)
            w = w / w.sum(axis=1, keepdims=True)
            return (self._kdtree_pts[idxs] * w[:, :, None]).sum(axis=1)
        else:
            fn = self._get_mesh_fn(self._kdtree_mesh or self._mesh_shape_name)
            out = np.empty_like(arr)
            for i in range(len(arr)):
                cp, _ = self._exact_point_normal(
                    fn,
                    float(arr[i][0]), float(arr[i][1]), float(arr[i][2]))
                out[i] = cp
            return out

    # ==================== 基础工具 ====================

    def _curve_is_closed(self, crv_name):
        try:
            return cmds.getAttr(f"{crv_name}.form") in (1, 2)
        except Exception:
            return False


    def _ridge_tangents(self, pts, closed):
        n = len(pts)
        if closed:
            t = np.roll(pts, -1, axis=0) - np.roll(pts, 1, axis=0)
        else:
            t = np.zeros_like(pts)
            t[1:-1] = pts[2:] - pts[:-2]
            t[0] = pts[1] - pts[0]
            t[-1] = pts[-1] - pts[-2]
        lens = np.linalg.norm(t, axis=1, keepdims=True)
        lens[lens < 1e-10] = 1.0
        return t / lens

    def _exact_point_normal(self, fn, x, y, z):
        """MFnMesh 精确求点和法线（无 KDTree 时使用）。

        fn 由调用方传入（已求值后的 MFnMesh），避免每次逐点重新拿。
        """
        if fn is None:
            return (np.array([x, y, z], dtype=float),
                    np.array([0.0, 0.0, 1.0], dtype=float))
        r = fn.getClosestPointAndNormal(om.MPoint(x, y, z), om.MSpace.kObject)
        cp, nv, _ = r
        return (np.array([cp.x, cp.y, cp.z]),
                np.array([nv.x, nv.y, nv.z]))

    # ==================== 滑动 ====================

    def _slide_point_kdtree(self, fn, p_start, T_start, distance):
        """KDTree 模式的单点滑动。
        ★ 全过程用 _query_fast（KDTree），不再调 MFnMesh。
        """
        p = np.array(p_start, dtype=float)
        T = np.array(T_start, dtype=float)
        step = distance / self.slide_steps

        # ★ 用 KDTree 拿初始点和法线
        p, N = self._query_fast(float(p[0]), float(p[1]), float(p[2]))
        ln = np.linalg.norm(N)
        if ln > 1e-10: N = N / ln
        T_cur = T - np.dot(T, N) * N
        lt = np.linalg.norm(T_cur)
        if lt > 1e-10: T_cur = T_cur / lt
        S = np.cross(N, T_cur)
        ls = np.linalg.norm(S)
        if ls < 1e-10: return p
        S = S / ls

        for i in range(self.slide_steps):
            p = p + S * step
            p_est, N_est = self._query_fast(float(p[0]), float(p[1]), float(p[2]))
            if p_est is not None:
                delta = p - p_est
                along = np.dot(delta, N_est)
                p = p - along * N_est
                N = N_est
            T_cur = T - np.dot(T, N) * N
            lt = np.linalg.norm(T_cur)
            if lt > 1e-10: T_cur = T_cur / lt
            S_new = np.cross(N, T_cur)
            ls = np.linalg.norm(S_new)
            if ls > 1e-10: S = S_new / ls

        # ★ 用 KDTree 拿最终点
        p_final, _ = self._query_fast(float(p[0]), float(p[1]), float(p[2]))
        return p_final

    def _slide_point_exact(self, fn, p_start, T_start, distance):
        """无 KDTree 时的精确滑动（顶点少时用）"""
        p = np.array(p_start, dtype=float)
        T = np.array(T_start, dtype=float)
        step = distance / self.slide_steps

        for i in range(self.slide_steps):
            p_surf, N = self._exact_point_normal(
                fn, float(p[0]), float(p[1]), float(p[2]))
            ln = np.linalg.norm(N)
            if ln > 1e-10: N = N / ln
            T_cur = T - np.dot(T, N) * N
            lt = np.linalg.norm(T_cur)
            if lt > 1e-10: T_cur = T_cur / lt
            S = np.cross(N, T_cur)
            ls = np.linalg.norm(S)
            if ls < 1e-10: break
            S = S / ls
            p = p_surf + S * step
            if (i + 1) % 5 == 0:
                p, _ = self._exact_point_normal(
                    fn, float(p[0]), float(p[1]), float(p[2]))

        p_final, _ = self._exact_point_normal(
            fn, float(p[0]), float(p[1]), float(p[2]))
        return p_final

    def _build_side(self, mesh_name, fn, ridge_pts, tangents, distance, side, closed,
                    rest_surface=None):
        n = len(ridge_pts)
        slide_pts = []
        for i in range(n):
            T0 = tangents[i] if side > 0 else -tangents[i]
            if self._use_kdtree:
                p = self._slide_point_kdtree(fn, ridge_pts[i], T0, distance)
            else:
                p = self._slide_point_exact(fn, ridge_pts[i], T0, distance)
            slide_pts.append(p)
        # No independent smoothing/resampling/refit here. Those operations
        # moved points along the ring and broke ridge[i] -> boundary[i].
        # The final periodic NURBS supplies C2 interpolation between these EPs.
        ordered = self._project_batch(np.asarray(slide_pts, dtype=float))
        if len(ordered) != len(ridge_pts) or not np.all(np.isfinite(ordered)):
            raise RuntimeError("Invalid corresponding boundary EPs")
        return ordered.tolist()




    def _make_curve(self, pts, name, closed, color):
        if len(pts) < 4:
            return None
        ep = [tuple(float(c) for c in p) for p in pts]

        if closed:
            # ★ 与 DualCurveBuilder 共用同一份周期曲线构造（含回退）
            crv = create_periodic_curve(ep, name)
            if crv is None:
                if cmds.objExists(name):
                    cmds.delete(name)
                crv = cmds.curve(d=3, ep=ep, name=name)
                try:
                    cmds.closeCurve(crv, ch=False, rpo=True, bb=0.3)
                except Exception:
                    pass
        else:
            if cmds.objExists(name):
                cmds.delete(name)
            crv = cmds.curve(d=3 if len(ep) >= 4 else 1, ep=ep, name=name)

        ColorScheme.set_color(crv, color)
        try:
            cmds.setAttr(f"{crv}.lineWidth", self.curve_line_width)
        except Exception:
            pass
        return crv

    # ==================== 骨骼 ====================

    def _create_joints(self, locs, ep_fwd, ep_bwd, n_ep, verbose):
        fwd_joints, bwd_joints = [], []
        ridge_eps = self._ridge_eps
        self._last_anchor_indices = []

        if verbose and ridge_eps is not None:
            log.info(f"骨骼索引对齐: 用脊线 EP（{len(ridge_eps)} 个）")

        # Validate all source assignments before creating/deleting any controls.
        indices=[]
        for loc in locs:
            lp = np.array(cmds.xform(loc, q=True, ws=True, t=True))
            if ridge_eps is None or len(ridge_eps)!=n_ep:
                raise RuntimeError("Boundary EP count differs from ridge EP count")
            idx=int(np.argmin(np.linalg.norm(ridge_eps-lp,axis=1)))
            if idx in indices:
                raise RuntimeError("Two main locs map to the same ridge EP; inspect main loc spacing")
            indices.append(idx)
        self._last_anchor_indices=indices
        for loc,idx in zip(locs,indices):
            fp = ep_fwd[idx]
            bp = ep_bwd[idx]

            fwd_name = loc + "_fwd"
            bwd_name = loc + "_bwd"
            if cmds.objExists(fwd_name): cmds.delete(fwd_name)
            if cmds.objExists(bwd_name): cmds.delete(bwd_name)

            cmds.select(clear=True)
            fj = cmds.joint(name=fwd_name,
                            position=tuple(float(c) for c in fp), radius=1.0)
            cmds.select(clear=True)
            bj = cmds.joint(name=bwd_name,
                            position=tuple(float(c) for c in bp), radius=1.0)

            # ★ 骨骼颜色
            ColorScheme.set_color(fj, self.fwd_joint_color)
            ColorScheme.set_color(bj, self.bwd_joint_color)

            fwd_joints.append(fj)
            bwd_joints.append(bj)
            if verbose:
                log.info(f"  {loc} -> EPidx={idx}")
        return fwd_joints, bwd_joints