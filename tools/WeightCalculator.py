# -*- coding: utf-8 -*-
"""WeightCalculator — V17.5.2 projection-style Bezier / unchanged strict range."""
import hashlib
from collections import deque, defaultdict

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import numpy as np
from time import perf_counter

from ..Logger import log
from .SurfaceRegion import solve_surface_band, RegionError
from .StrictCurveRegion import build_strict_region, weight_coordinates, _wct_shape
from .LegacyBoundaryAlgorithms import LegacyBoundaryAlgorithmsMixin

try:
    from scipy.spatial import cKDTree
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ========================== V16.9 Bezier 权重工具 ==========================

def _bezier_v(p, t):
    """
    三次 Bezier 曲线求值。

    p: 四个控制点
    t: Bezier 参数，范围 0~1
    """
    t = float(t)
    omt = 1.0 - t

    return (
        p[0] * omt ** 3
        + 3.0 * p[1] * t * omt ** 2
        + 3.0 * p[2] * t ** 2 * omt
        + p[3] * t ** 3
    )


def _bezier_t(p, v, tol=1e-7, max_iter=48):
    """
    根据 Bezier 曲线的 X 值反解参数 t。

    使用二分法，要求 X 控制点单调递增。
    """
    v = float(v)

    lo = 0.0
    hi = 1.0

    for _ in range(max_iter):
        mid = (lo + hi) * 0.5
        value = _bezier_v(p, mid)
        error = value - v

        if abs(error) <= tol:
            return mid

        if error > 0.0:
            hi = mid
        else:
            lo = mid

    return (lo + hi) * 0.5


def _bezier_v_array(p, t_arr):
    """三次 Bezier 曲线数组求值。"""
    t = np.asarray(t_arr, dtype=np.float64)
    omt = 1.0 - t

    return (
        p[0] * omt ** 3
        + 3.0 * p[1] * t * omt ** 2
        + 3.0 * p[2] * t ** 2 * omt
        + p[3] * t ** 3
    )


def _bezier_t_array(p, v_arr, tol=1e-7, max_iter=48):
    """
    Bezier X 曲线数组反解。

    与 _bezier_t 使用相同的二分算法和误差容限。
    不使用 LUT，不进行 np.interp 近似。
    """
    values = np.asarray(v_arr, dtype=np.float64)

    lo = np.zeros_like(values, dtype=np.float64)
    hi = np.ones_like(values, dtype=np.float64)

    for _ in range(max_iter):
        mid = (lo + hi) * 0.5
        curve_value = _bezier_v_array(p, mid)
        error = curve_value - values

        converged = np.abs(error) <= tol
        active = ~converged

        if not np.any(active):
            break

        move_hi = active & (error > 0.0)
        move_lo = active & (error <= 0.0)

        hi = np.where(move_hi, mid, hi)
        lo = np.where(move_lo, mid, lo)

    return (lo + hi) * 0.5


def get_weight(x, xs, ys):
    """
    V16.9 Bezier 标量权重映射。

    先通过 xs 反解参数 t，
    再使用同一个 t 计算 ys。

    xs、ys 均由 UI 实际传入，不使用硬编码控制点。
    """
    xs = tuple(float(v) for v in xs)
    ys = tuple(float(v) for v in ys)
    x = float(x)

    if x <= 0.0:
        return float(ys[0])

    if x >= 1.0:
        return float(ys[3])

    t = _bezier_t(xs, x)
    return float(_bezier_v(ys, t))


def get_weight_array(x_arr, xs, ys):
    """
    V16.9 Bezier 数组权重映射。

    与 get_weight 使用相同的数学过程：
        1. xs 反解 t
        2. ys 根据 t 求值

    不使用 LUT，不使用 np.interp。
    """
    xs = tuple(float(v) for v in xs)
    ys = tuple(float(v) for v in ys)

    values = np.asarray(x_arr, dtype=np.float64)

    if values.size == 0:
        return values.copy()

    # 与标量版本保持相同的端点规则。
    t = _bezier_t_array(xs, values)

    result = _bezier_v_array(ys, t)

    result = np.where(values <= 0.0, ys[0], result)
    result = np.where(values >= 1.0, ys[3], result)

    return np.asarray(result, dtype=np.float64)


def _boundary_bezier_fractions(t, side, ridge, xs, ys, mode):
    """Projection-style easing, independent of strict geometric membership.

    Soft evaluates ridge-to-boundary distance once. IK evaluates the SINGLE
    BWD(0)->ridge(.5)->FWD(1) coordinate, not two copies of a full easing.
    Keep the user's fixed 0/.5/1 anchors: for asymmetric handles rescale Y on
    either side of the evaluated midpoint; never restart the Bezier X range.
    Symmetric standard endpoints give exactly the projection mapping.
    """
    t=np.asarray(t,dtype=float);side=np.asarray(side);ridge=np.asarray(ridge,dtype=bool)
    xs=np.asarray(xs,dtype=float);ys=np.asarray(ys,dtype=float)
    if (xs.shape!=(4,) or ys.shape!=(4,) or not np.all(np.isfinite(xs))
            or not np.all(np.isfinite(ys)) or not np.all(np.isfinite(t))
            or side.shape!=t.shape or ridge.shape!=t.shape):
        raise RuntimeError('Invalid boundary Bezier controls/coordinates')
    distance=np.clip(t,0.,1.);span=float(ys[-1]-ys[0])
    def ease(values):
        if abs(span)<=1e-8:return np.asarray(values,dtype=float).copy()
        return np.clip((get_weight_array(values,xs,ys)-ys[0])/span,0.,1.)
    if mode=='soft':
        fraction=1.-ease(distance)
        fraction[ridge]=1.
    elif mode=='ik':
        coordinate=.5+.5*np.where(side>0,distance,-distance)
        eased=ease(coordinate)
        midpoint=float(ease(np.array([.5]))[0])
        # Only output magnitudes are anchored for non-mirrored controls.
        # This avoids a jump at the mandatory 50/50 ridge.
        low=(.5*eased/midpoint if midpoint>1e-8 else coordinate)
        high=(.5+.5*(eased-midpoint)/(1.-midpoint) if midpoint<1.-1e-8 else coordinate)
        fraction=np.where(coordinate<=.5,np.clip(low,0.,.5),np.clip(high,.5,1.))
        fraction[ridge]=.5
    else:raise RuntimeError('Unknown boundary weight mode: '+str(mode))
    return np.clip(fraction,0.,1.)


# ========================== 圆弧切线 Hermite 采样 ==========================

def _unit(v):
    ln = np.linalg.norm(v)
    return v / ln if ln > 1e-9 else np.zeros_like(v)


def _fit_circle_3pts(p1, p2, p3):
    pts = np.array([p1, p2, p3], dtype=float)
    centroid = pts.mean(axis=0)
    centered = pts - centroid
    try:
        _, s, Vt = np.linalg.svd(centered, full_matrices=False)
    except Exception:
        return None
    if len(s) >= 2 and s[1] < 1e-8 * max(s[0], 1e-12):
        return None
    u, v = Vt[0], Vt[1]
    x, y = centered @ u, centered @ v
    A = np.column_stack([x, y, np.ones(len(pts))])
    b = -(x * x + y * y)
    try:
        D, E, F = np.linalg.lstsq(A, b, rcond=None)[0]
    except Exception:
        return None
    cx, cy = -D / 2.0, -E / 2.0
    r2 = cx * cx + cy * cy - F
    if r2 <= 1e-12:
        return None
    return centroid + cx * u + cy * v, float(np.sqrt(r2)), u, v


def _arc_tangent_unit(p_prev, p_curr, p_next):
    if p_prev is None and p_next is None:
        return np.zeros(3, dtype=float)
    if p_prev is None:
        return _unit(p_next - p_curr)
    if p_next is None:
        return _unit(p_curr - p_prev)
    fit = _fit_circle_3pts(p_prev, p_curr, p_next)
    if fit is None:
        return _unit(p_next - p_prev)
    center, radius, u, v = fit
    radial = p_curr - center
    normal = np.cross(u, v)
    ln = np.linalg.norm(normal)
    if ln < 1e-9:
        return _unit(p_next - p_prev)
    normal = normal / ln
    tangent = np.cross(normal, radial)
    if np.dot(tangent, p_next - p_curr) < 0:
        tangent = -tangent
    return _unit(tangent)


def _segment_arc_params(p_a, p_b, p_helper):
    fit = _fit_circle_3pts(p_a, p_b, p_helper)
    if fit is None:
        return 0.0, 0.0
    center, radius, u, v = fit
    r_a = p_a - center
    r_b = p_b - center
    ln_a = np.linalg.norm(r_a)
    ln_b = np.linalg.norm(r_b)
    if ln_a < 1e-9 or ln_b < 1e-9:
        return 0.0, 0.0
    cos_theta = float(np.dot(r_a, r_b) / (ln_a * ln_b))
    cos_theta = max(-1.0, min(1.0, cos_theta))
    return radius, float(np.arccos(cos_theta))


def _hermite_chain(anchors, closed, samples_per_segment):
    pts = [np.asarray(p, dtype=float) for p in anchors]
    n = len(pts)
    if n < 2:
        return [p.copy() for p in pts]
    dirs = []
    for i in range(n):
        if closed and n >= 3:
            p_prev = pts[(i - 1) % n]
            p_curr = pts[i]
            p_next = pts[(i + 1) % n]
        else:
            p_curr = pts[i]
            p_prev = pts[i - 1] if i > 0 else None
            p_next = pts[i + 1] if i + 1 < n else None
        dirs.append(_arc_tangent_unit(p_prev, p_curr, p_next))
    seg_count = n if closed else (n - 1)
    seg_params = []
    for i in range(seg_count):
        p_a = pts[i % n]
        p_b = pts[(i + 1) % n]
        if closed and n >= 3:
            p_helper = pts[(i + 2) % n]
        else:
            if i + 2 < n:
                p_helper = pts[i + 2]
            elif i > 0:
                p_helper = pts[i - 1]
            else:
                p_helper = None
        if p_helper is None:
            seg_params.append((0.0, 0.0))
        else:
            seg_params.append(_segment_arc_params(p_a, p_b, p_helper))
    result = []
    for i in range(seg_count):
        p0 = pts[i % n]
        p1 = pts[(i + 1) % n]
        d0 = dirs[i % n]
        d1 = dirs[(i + 1) % n]
        R, theta = seg_params[i]
        chord = float(np.linalg.norm(p1 - p0))
        L = 4.0 * np.tan(theta / 4.0) * R if (R > 1e-9 and theta > 1e-6) else chord
        m0 = d0 * L
        m1 = d1 * L
        for j in range(samples_per_segment):
            s = j / samples_per_segment
            s2 = s * s
            s3 = s2 * s
            h00 = 2 * s3 - 3 * s2 + 1
            h10 = s3 - 2 * s2 + s
            h01 = -2 * s3 + 3 * s2
            h11 = s3 - s2
            p = h00 * p0 + h10 * m0 + h01 * p1 + h11 * m1
            result.append(p)
    result.append(pts[0].copy() if closed else pts[-1].copy())
    return result


def _get_skin_influence_weights(mesh_name, joint_name, expected_count):
    """严格读取指定 Joint 的逐顶点 SkinCluster 权重。"""

    if not joint_name:
        raise RuntimeError("joint_name 为空")

    if not cmds.objExists(joint_name):
        raise RuntimeError(
            "Joint 不存在: {}".format(joint_name)
        )

    if cmds.nodeType(joint_name) != "joint":
        raise RuntimeError(
            "目标不是 joint: {} | type={}".format(
                joint_name, cmds.nodeType(joint_name)
            )
        )

    shapes = cmds.listRelatives(
        mesh_name,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
        type="mesh"
    ) or []

    if not shapes:
        raise RuntimeError(
            "找不到 Mesh Shape: {}".format(mesh_name)
        )

    skin_clusters = cmds.ls(
        cmds.listHistory(mesh_name, pruneDagObjects=True) or [],
        type="skinCluster"
    ) or []

    if not skin_clusters:
        raise RuntimeError(
            "模型没有 SkinCluster: {}".format(mesh_name)
        )

    joint_long_list = cmds.ls(
        joint_name, long=True, type="joint"
    ) or []

    if len(joint_long_list) != 1:
        raise RuntimeError(
            "Joint 长路径解析不唯一: {} -> {}".format(
                joint_name, joint_long_list
            )
        )

    joint_long = joint_long_list[0]

    errors = []

    for skin_name in skin_clusters:
        try:
            sel_skin = om.MSelectionList()
            sel_skin.add(skin_name)

            skin_fn = oma.MFnSkinCluster(
                sel_skin.getDependNode(0)
            )

            influence_paths = skin_fn.influenceObjects()

            influence_index = None
            matched_path = None

            # 只允许完整 DAG 路径精确匹配
            for i, dag in enumerate(influence_paths):
                path_name = dag.fullPathName()

                if path_name == joint_long:
                    influence_index = i
                    matched_path = path_name
                    break

            if influence_index is None:
                available = [
                    dag.fullPathName()
                    for dag in influence_paths
                ]

                raise RuntimeError(
                    "Joint 不是该 SkinCluster 的 Influence: {}\n"
                    "目标完整路径: {}\n"
                    "当前 Influences: {}".format(
                        joint_name,
                        joint_long,
                        available
                    )
                )

            sel_mesh = om.MSelectionList()
            sel_mesh.add(shapes[0])

            mesh_path = sel_mesh.getDagPath(0)

            comp_fn = om.MFnSingleIndexedComponent()
            component = comp_fn.create(
                om.MFn.kMeshVertComponent
            )

            comp_fn.addElements(
                list(range(int(expected_count)))
            )

            values = skin_fn.getWeights(
                mesh_path,
                component,
                int(influence_index)
            )

            result = np.asarray(
                list(values), dtype=float
            )

            if result.size != int(expected_count):
                raise RuntimeError(
                    "权重数量不匹配: {} != {}".format(
                        result.size, expected_count
                    )
                )

            log.info(
                "[权重读取] 成功: joint={}, skin={}, "
                "physicalIndex={}, vertices={}".format(
                    matched_path,
                    skin_name,
                    influence_index,
                    result.size
                )
            )

            return np.clip(result, 0.0, 1.0)

        except Exception as exc:
            errors.append(
                "{}: {}".format(skin_name, exc)
            )

    raise RuntimeError(
        "读取 Joint 权重失败: {}\n{}".format(
            joint_long,
            "\n".join(errors)
        )
    )


# ========================== 主类 ==========================

class WeightCalculator(LegacyBoundaryAlgorithmsMixin):

    def __init__(self, curve_suffix="laplacian", curve_samples=200,
                 use_curve=True):
        self.mode = "soft"
        self.samples_per_segment = 30
        self.curve_suffix = curve_suffix
        self.curve_samples = int(curve_samples)
        self.use_curve = bool(use_curve)
        self._boundary_cache = {}
        self._surface_region_cache = {}
        self._bind_verts_cache = {}
        self._graph_cache = {}
        self._face_graph_cache = {}

    def set_mode(self, mode):
        """切换权重模式：soft（脊线峰值双侧衰减）或 ik（BWD 到 FWD 递增）。"""
        mode = str(mode).lower()
        if mode in ("soft", "ik"):
            self.mode = mode
            log.ok("权重计算模式切换为: {}".format(mode))
            return True
        log.warn("不支持的权重计算模式: {}（仅支持 soft / ik）".format(mode))
        return False

    def get_mode(self):
        return self.mode

    def set_curve_suffix(self, suffix):
        if suffix not in ("laplacian", "gaussian"):
            log.warn("无效曲线后缀: {}".format(suffix))
            return False
        self.curve_suffix = suffix
        return True

    def set_use_curve(self, flag):
        self.use_curve = bool(flag)
        return True

    def clear_boundary_cache(self):
        self._boundary_cache.clear()
        self._surface_region_cache.clear()
        
    def clear_all_cache(self):
        self._boundary_cache.clear()
        self._surface_region_cache.clear()
        self._bind_verts_cache.clear()
        self._graph_cache.clear()
        self._face_graph_cache.clear()

    def compute(self, mesh_name, joints, xs=None, ys=None,
                radius=None, closed=False, verbose=True,
                side_locs=None, rest_surface=None, return_mask=False,
                parent_joint=None, child_joint=None, curve_names=None):
        if xs is None:
            xs = (0.0, 0.33, 0.67, 1.0)
        if ys is None:
            ys = (0.0, 0.0, 1.0, 1.0)
        xs = tuple(float(v) for v in xs)
        ys = tuple(float(v) for v in ys)

        if not cmds.objExists(mesh_name):
            if verbose:
                log.warn("网格 '{}' 不存在".format(mesh_name))
            return None

        joints = self._normalize(joints)
        if not joints:
            if verbose:
                log.warn("至少需要 1 个 joint")
            return None
        # joints 在此处是 Ridge locator / 几何采样锚点列表，
        # 不是 skin influence；只检查场景节点是否存在，不要求 nodeType=joint。
        valid = [loc for loc in joints if cmds.objExists(loc)]
        if not valid:
            if verbose:
                log.warn("没有有效的 Ridge locator / 采样锚点")
            return None
        joints = valid

        # 父骨骼不在 WeightCalculator 内部推断。
        # 由 core.pick_reference_joint() 调用 JointState.get_parent_joint()，
        # 沿骨骼链筛选当前 mesh 的 skinCluster influence 后，
        # 通过 parent_joint 参数显式传入。

        n_verts = self._get_mesh_vert_count(mesh_name)
        if n_verts is None or n_verts == 0:
            if verbose:
                log.warn("拿不到网格顶点数")
            return None

        if (side_locs is not None and len(side_locs) == 2
                and rest_surface is not None):
            fwd_locs, bwd_locs = side_locs
            if len(fwd_locs) >= 2 and len(bwd_locs) >= 2:
                if not parent_joint:
                    if verbose:
                        log.warn(
                            "未收到已筛选的父级蒙皮骨骼。请从 core / JointState "
                            "传入 parent_joint；WeightCalculator 不再自行查找父骨骼。")
                    return None
                return self._compute_boundary_mode_loft(
                    mesh_name, joints, fwd_locs, bwd_locs,
                    xs, ys, closed, verbose, rest_surface,
                    return_mask=return_mask,
                    parent_joint=parent_joint,
                    child_joint=child_joint,
                    curve_names=curve_names)

        if side_locs is not None:
            log.warn("边界模式缺少有效的两侧 locator；取消计算")
            return None
        if rest_surface is not None:
            return self._compute_fallback_mode_v2(
                mesh_name, joints, radius, xs, ys, closed, verbose, rest_surface)

        return None   

    def _compute_boundary_mode_loft(
            self, mesh_name, joints, fwd_locs, bwd_locs,
            xs, ys, closed, verbose, rs,
            return_mask=False, parent_joint=None, child_joint=None, curve_names=None):
        """The user-validated current-pose strict band is shared by Soft/IK.

        No face unfolding, rest-spline reconstruction or DTW is used here.
        Only IK additionally writes the complete connected FWD exterior.
        """
        try:
            if not parent_joint or not child_joint:
                raise RuntimeError("缺少父/子蒙皮骨骼")
            if curve_names is None:
                names = []
                for kind in ('main', 'fwd', 'bwd'):
                    prefix = mesh_name if kind == 'main' else mesh_name + '_' + kind
                    candidates = [prefix + '_ring_' + suffix for suffix in
                                  (self.curve_suffix, 'laplacian', 'gaussian')]
                    name = next((name for name in candidates if cmds.objExists(name)), None)
                    if name is None:
                        raise RuntimeError("缺少 {} 曲线".format(kind))
                    names.append(name)
                curve_names = tuple(names)
            mesh_fn = om.MFnMesh(_wct_shape(mesh_name, 'mesh'))
            verts = mesh_fn.getPoints(om.MSpace.kWorld)
            n = mesh_fn.numVertices
            digest = hashlib.blake2b(digest_size=20)
            for data in (np.asarray([(p.x, p.y, p.z) for p in verts], dtype=float),
                         np.asarray(mesh_fn.getVertices()[0], dtype=np.int64),
                         np.asarray(mesh_fn.getVertices()[1], dtype=np.int64)):
                digest.update(data.tobytes())
            for name in curve_names:
                fn = om.MFnNurbsCurve(_wct_shape(name, 'nurbsCurve'))
                cvs = fn.cvPositions(om.MSpace.kWorld)
                digest.update(np.asarray([(p.x, p.y, p.z, p.w) for p in cvs], dtype=float).tobytes())
                digest.update(np.asarray(fn.knots(), dtype=float).tobytes())
                digest.update(str((fn.degree, fn.form)).encode('ascii'))
            key = (mesh_name, tuple(curve_names), digest.digest())
            cached = self._surface_region_cache.get(key)
            if cached is None:
                stage = perf_counter()
                region = build_strict_region(mesh_name, tuple(curve_names))
                region_time = perf_counter() - stage
                stage = perf_counter()
                t, side, ridge = weight_coordinates(region, mesh_name)
                log.info("[WCT 范围耗时] 严格范围={:.3f}s 衰减坐标={:.3f}s".format(
                    region_time, perf_counter()-stage))
                cached = region, t, side, ridge
                if len(self._surface_region_cache) >= 3:
                    self._surface_region_cache.clear()
                self._surface_region_cache[key] = cached
            region, t, side, ridge = cached
            self.last_decay_t = t.copy()
            self.last_decay_side = side.copy()
            self.last_boundary_band_mask = region['mask'].copy()
            self.last_boundary_fwd_outer_mask = region['fwd_outer'].copy()
            parent = _get_skin_influence_weights(mesh_name, parent_joint, n)
            child = _get_skin_influence_weights(mesh_name, child_joint, n)
            cap = np.clip(parent + child, 0.0, 1.0)
            normalized = _boundary_bezier_fractions(t, side, ridge, xs, ys, self.mode)
            outer = np.zeros(n, dtype=bool)
            if self.mode == 'ik':
                if region['outer_error']:
                    raise RuntimeError(region['outer_error'])
                outer = region['fwd_outer'].copy()
                normalized[outer] = 1.0
            self.last_boundary_fraction = normalized.copy()
            valid_mask = region['mask'] | outer
            weights = np.where(valid_mask, np.clip(normalized, 0.0, 1.0) * cap, 0.0)
            if verbose:
                for label, direction in (('FWD', 1), ('BWD', -1)):
                    ids = region['mask'] & (side == direction) & ~ridge
                    if np.any(ids):
                        log.info("[衰减 V17.5.2] {}: points={}, normalized={:.5f}..{:.5f}, "
                                 "pair_cap={:.5f}..{:.5f}, picked={:.5f}..{:.5f}".format(
                                     label, int(ids.sum()), float(normalized[ids].min()),
                                     float(normalized[ids].max()), float(cap[ids].min()),
                                     float(cap[ids].max()), float(weights[ids].min()),
                                     float(weights[ids].max())))
                log.info("[严格范围 V17.3] mode={}, band={}, FWD_outer={}, write={}/{}, "
                         "on_curve={}, uncertain={}".format(
                             self.mode, int(region['mask'].sum()), int(outer.sum()),
                             int(valid_mask.sum()), n, region['on_curve'], region['uncertain']))
            return (weights, valid_mask) if return_mask else weights
        except Exception as exc:
            log.warn("[严格范围 V17.3] 计算取消，未写入权重: {}".format(exc))
            return None


    # ==================== v16.4 专用：复刻 densify_project ====================


    # ==================== v16.4 拓扑切割 ====================


    # ==================== v16.4 辅助 ====================


    def _read_curve_local(self, mesh_name, kind):
        from . import CurveLocalPoints
        if kind == "main":
            names = ["{}_ring_laplacian".format(mesh_name),
                     "{}_ring_gaussian".format(mesh_name)]
        else:
            names = ["{}_{}_ring_laplacian".format(mesh_name, kind),
                     "{}_{}_ring_gaussian".format(mesh_name, kind)]
        for name in names:
            pts = CurveLocalPoints.read_local(name)
            if pts is not None and len(pts) >= 2:
                return pts
        return None

    def _locs_to_rest_samples(self, mesh_name, locs, closed, rs):
        anchor_pts = []
        for loc in locs:
            p = rs.get_joint_rest_position(loc)
            if p is None:
                return None
            anchor_pts.append(p)
        if len(anchor_pts) < 2:
            return None
        samples_per_seg = max(2, 200 // max(1, len(anchor_pts) - 1))
        dense = _hermite_chain(anchor_pts, closed, samples_per_seg)
        dense = np.asarray(dense, dtype=float)
        n = len(dense)
        if n > 200:
            idx = np.linspace(0, n - 1, 200).astype(int)
            dense = dense[idx]
        return dense


    # ==================== 8D 回退 ====================

    def _compute_fallback_mode_v2(self, mesh_name, joints, radius, xs, ys,
                                  closed, verbose, rs):
        if len(joints) == 1:
            p = rs.get_joint_rest_position(joints[0])
            if p is not None:
                r = radius if (radius and radius > 0) else (
                    self._get_maya_soft_radius() or 1.0)
                raw = rs.solve_point_raw(p, radius=r)
                t = 1.0 - raw
                w_raw = get_weight_array(t, xs, ys)
                return np.clip(1.0 - w_raw, 0.0, 1.0)

        ridge_pts = self._read_curve_local(mesh_name, "main")
        if ridge_pts is None:
            ridge_pts = self._locs_to_rest_samples(mesh_name, joints, closed, rs)
        if ridge_pts is None or len(ridge_pts) < 2:
            return None

        r = radius if (radius and radius > 0) else (
            self._get_maya_soft_radius() or 1.0)
        raw = rs.solve_curve_raw(ridge_pts, close=closed, radius=r)
        t = 1.0 - raw
        w_raw = get_weight_array(t, xs, ys)
        return np.clip(1.0 - w_raw, 0.0, 1.0)

    # ==================== 基础工具 ====================

    def _normalize(self, joints):
        if isinstance(joints, str):
            return [joints]
        try:
            return [str(jnt) for jnt in joints]
        except TypeError:
            return [str(joints)]

    def _get_mesh_vert_count(self, mesh_name):
        try:
            return int(cmds.polyEvaluate(mesh_name, vertex=True))
        except Exception:
            shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
            if not shapes:
                return None
            try:
                sel = om.MSelectionList(); sel.add(shapes[0])
                fn = om.MFnMesh(sel.getDagPath(0))
                return int(fn.numVertices)
            except Exception:
                return None

    def _get_maya_soft_radius(self):
        try:
            r = cmds.softSelect(query=True, softSelectDistance=True)
            if r and r > 0:
                return float(r)
        except Exception:
            pass
        return None                              
