# -*- coding: utf-8 -*-
"""CurveRefine —— 曲线通用算法 + mesh 投影 + EP 精修

★ 性能关键：
  · get_mesh_fn 构造 MFnMesh 会触发 worldMesh 求值（含 skinCluster 变形），
    非常昂贵。所有循环都必须在循环外构造一次，循环内复用。
  · 单点 API（project_to_mesh / surface_normal）保留，供零散调用。
  · 批量 API（soft_project / offset_adaptive / offset_normal）内部走复用路径。
"""
import maya.cmds as cmds
import maya.api.OpenMaya as om
import numpy as np
from collections import OrderedDict

try:
    from scipy.spatial import cKDTree
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ============================================================================
# 缓存
# ============================================================================

_PROJ_CACHE = OrderedDict()
_PROJ_CACHE_MAX = 50000


def get_mesh_fn(shape_name):
    """拿**求值后**的 MFnMesh（worldMesh plug）。

    ★ 每次构造会触发 worldMesh 求值（含 skinCluster）——非常昂贵。
      循环中务必只调一次，把返回值传下去。
    """
    if not shape_name or not cmds.objExists(shape_name):
        return None
    try:
        sel = om.MSelectionList()
        sel.add(shape_name)
        dag = sel.getDagPath(0)
        dep = om.MFnDependencyNode(dag.node())
        plug = dep.findPlug("worldMesh", False).elementByLogicalIndex(0)
        return om.MFnMesh(plug.asMObject())
    except Exception:
        return None


def clear_mesh_fn_cache():
    """清空投影位置缓存（mesh 变形后必须清）。"""
    _PROJ_CACHE.clear()


def _proj_cache_get(key):
    value = _PROJ_CACHE.get(key)
    if value is not None:
        _PROJ_CACHE.move_to_end(key)
    return value


def _proj_cache_put(key, value):
    _PROJ_CACHE[key] = value
    _PROJ_CACHE.move_to_end(key)
    while len(_PROJ_CACHE) > _PROJ_CACHE_MAX:
        _PROJ_CACHE.popitem(last=False)


def _closest_point(fn, pt):
    """用给定 MFnMesh 求最近点。"""
    p = om.MPoint(float(pt[0]), float(pt[1]), float(pt[2]))
    c, _ = fn.getClosestPoint(p, om.MSpace.kWorld)
    return np.array([c.x, c.y, c.z])


def _closest_point_normal(fn, pt):
    """一次调用拿最近点 + 法线。"""
    p = om.MPoint(float(pt[0]), float(pt[1]), float(pt[2]))
    c, n, _ = fn.getClosestPointAndNormal(p, om.MSpace.kWorld)
    return (np.array([c.x, c.y, c.z]), np.array([n.x, n.y, n.z]))


# ============================================================================
# 强度预设
# ============================================================================

STRENGTH_PRESETS = {
    "light":   {"lap_iter": 2,  "lap_w": 0.3,  "gauss_sigma": 1.5, "gauss_r": 4,  "adhesion": 0.4},
    "medium":  {"lap_iter": 5,  "lap_w": 0.5,  "gauss_sigma": 3.0, "gauss_r": 8,  "adhesion": 0.6},
    "strong":  {"lap_iter": 12, "lap_w": 0.7,  "gauss_sigma": 6.0, "gauss_r": 15, "adhesion": 0.85},
    "extreme": {"lap_iter": 30, "lap_w": 0.9,  "gauss_sigma": 12.0,"gauss_r": 30, "adhesion": 1.0},
}


# ============================================================================
# 单点投影（供零散调用）
# ============================================================================

def project_to_mesh(shape_name, point):
    """投影到 mesh 表面（精确投影 + 位置缓存）。

    ★ 单点调用，内部构造 MFnMesh。批量调用请用 soft_project。
    """
    pt = np.asarray(point, dtype=float)
    key = (shape_name,
           round(float(pt[0]), 4), round(float(pt[1]), 4), round(float(pt[2]), 4))
    cached = _proj_cache_get(key)
    if cached is not None:
        return cached.copy()

    fn = get_mesh_fn(shape_name)
    if fn is None:
        return None

    try:
        result = _closest_point(fn, pt)
    except Exception:
        return None

    _proj_cache_put(key, result)
    return result.copy()


def surface_normal(shape_name, point):
    fn = get_mesh_fn(shape_name)
    if fn is None:
        return None
    try:
        _, n = _closest_point_normal(fn, point)
        ln = np.linalg.norm(n)
        return n / ln if ln > 1e-10 else None
    except Exception:
        return None


# ============================================================================
# 几何算法
# ============================================================================

def fit_circle_3d(points):
    pts = np.array(points, dtype=float)
    if len(pts) < 3:
        return None
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


def _unit(v):
    ln = np.linalg.norm(v)
    return v / ln if ln > 1e-9 else np.zeros(3, dtype=float)


def _arc_tangent_unit(p_prev, p_curr, p_next):
    if p_prev is None and p_next is None:
        return np.zeros(3, dtype=float)
    if p_prev is None:
        return _unit(p_next - p_curr)
    if p_next is None:
        return _unit(p_curr - p_prev)

    fit = fit_circle_3d([p_prev, p_curr, p_next])
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
    fit = fit_circle_3d([p_a, p_b, p_helper])
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
    theta = float(np.arccos(cos_theta))
    return radius, theta


def _hermite_chain(anchors, closed, samples_per_segment):
    """圆弧切线 Hermite 插值。

    切线长度 = 4·tan(θ/4)·R（三次 Bézier 圆弧等价），不是 chord。
    """
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

        if R > 1e-9 and theta > 1e-6:
            L = 4.0 * np.tan(theta / 4.0) * R
        else:
            L = chord

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


def make_skeleton(points, closed, samples_per_segment):
    pts = [np.asarray(p, dtype=float) for p in points]
    n = len(pts)
    if n < 2:
        return None, None
    if n == 2:
        dense = [pts[0] + (pts[1] - pts[0]) * (j / samples_per_segment)
                 for j in range(samples_per_segment + 1)]
        return dense, {0, len(dense) - 1}

    dense = _hermite_chain(pts, closed, samples_per_segment)

    anchors = set()
    for i in range(0, len(dense) - 1, samples_per_segment):
        anchors.add(i)
    anchors.add(len(dense) - 1)

    return dense, anchors


def soft_project(shape_name, points, anchors, adhesion):
    """按 adhesion 比例把点拉向 mesh 表面投影。

    ★ 循环外构造一次 MFnMesh，循环内复用。
    """
    if adhesion <= 0:
        return [np.asarray(p, dtype=float) for p in points]

    fn = get_mesh_fn(shape_name)
    if fn is None:
        return [np.asarray(p, dtype=float) for p in points]

    out = []
    for i, p in enumerate(points):
        p_arr = np.asarray(p, dtype=float)
        if i in anchors:
            out.append(p_arr)
            continue
        try:
            pj = _closest_point(fn, p_arr)
        except Exception:
            pj = None
        out.append(p_arr if pj is None else (1 - adhesion) * p_arr + adhesion * pj)
    return out


def laplacian(points, anchors, iters, weight):
    if iters <= 0 or weight <= 0:
        return [np.asarray(p, dtype=float) for p in points]
    arr = np.asarray([np.asarray(p, dtype=float) for p in points], dtype=float)
    n = len(arr)
    if n < 3:
        return [row.copy() for row in arr]

    skip = np.zeros(n, dtype=bool)
    valid_anchors = [i for i in anchors if 0 <= i < n]
    if valid_anchors:
        skip[valid_anchors] = True
    skip[0] = True
    skip[-1] = True
    inner_mask = ~skip[1:-1]

    mid_buf = np.empty_like(arr[:-2])
    new_buf = np.empty_like(arr[1:-1])
    one_minus_w = 1.0 - weight
    half_w = 0.5 * weight

    for _ in range(iters):
        np.add(arr[:-2], arr[2:], out=mid_buf)
        mid_buf *= half_w
        np.multiply(arr[1:-1], one_minus_w, out=new_buf)
        new_buf += mid_buf
        # ★ 修复：arr[1:-1] 是 view，用 np.copyto 写入 view 而不是 fancy indexing
        np.copyto(arr[1:-1], new_buf, where=inner_mask[:, None])

    return [row.copy() for row in arr]


def gaussian(deltas, anchors, sigma, radius, closed):
    n = len(deltas)
    if sigma <= 0 or n == 0:
        return [list(d) for d in deltas]
    arr = np.asarray(deltas, dtype=float)

    ks = np.arange(-radius, radius + 1)
    ws = np.exp(-0.5 * (ks / sigma) ** 2)

    if closed:
        out = np.zeros_like(arr)
        for idx, k in enumerate(ks):
            out += ws[idx] * np.roll(arr, -k, axis=0)
        out = out / ws.sum()
    else:
        out = np.zeros_like(arr)
        idxs = np.arange(n)
        wsum = np.zeros(n)
        for idx, k in enumerate(ks):
            src = idxs + k
            valid = (src >= 0) & (src < n)
            out[valid] += ws[idx] * arr[src[valid]]
            wsum[valid] += ws[idx]
        wsum[wsum < 1e-12] = 1.0
        out = out / wsum[:, None]

    for i in anchors:
        if 0 <= i < n:
            out[i] = 0.0

    return out.tolist()


def simplify_by_length(points, n):
    pts = np.array(points)
    if len(pts) <= n:
        return pts.tolist()
    lens = [0.0]
    for i in range(1, len(pts)):
        lens.append(lens[-1] + np.linalg.norm(pts[i] - pts[i - 1]))
    total = lens[-1]
    if total < 1e-10:
        return pts[:n].tolist()
    result = []
    for i in range(n):
        t = i / (n - 1) * total
        for j in range(1, len(lens)):
            if lens[j] >= t:
                ratio = (t - lens[j - 1]) / (lens[j] - lens[j - 1] + 1e-10)
                result.append((pts[j - 1] + ratio * (pts[j] - pts[j - 1])).tolist())
                break
    return result


def simplify_by_anchors(dense, anchors, internal):
    dense = [np.asarray(p, dtype=float) for p in dense]
    if not dense or not anchors:
        return []
    alist = sorted(anchors)
    n_sample = internal + 2
    result = []
    for i in range(len(alist) - 1):
        s, e = alist[i], alist[i + 1]
        seg = dense[s:e + 1]
        pts = [p.tolist() for p in seg] if len(seg) <= n_sample else simplify_by_length(seg, n_sample)
        if result:
            pts = pts[1:]
        result.extend(pts)
    return result


def offset_radial(points, center, offset):
    if abs(offset) < 1e-12:
        return [np.asarray(p, dtype=float) for p in points]
    center = np.asarray(center, dtype=float)
    out = []
    for p in points:
        p_arr = np.asarray(p, dtype=float)
        v = p_arr - center
        ln = np.linalg.norm(v)
        out.append(p_arr + offset * (v / ln) if ln > 1e-10 else p_arr)
    return out


def offset_adaptive(shape_name, points, center, min_off, max_off, margin):
    """沿 mesh 表面法向抬升（循环外复用 MFnMesh）。"""
    fn = get_mesh_fn(shape_name)
    if fn is None:
        return [np.asarray(p, dtype=float) for p in points]

    out = []
    for p in points:
        p_arr = np.asarray(p, dtype=float)
        try:
            pj, nv = _closest_point_normal(fn, p_arr)
        except Exception:
            out.append(p_arr)
            continue
        ln = np.linalg.norm(nv)
        if ln < 1e-10:
            out.append(p_arr)
            continue
        n = nv / ln
        d = pj - p_arr
        along = float(d @ n)
        offset = (np.linalg.norm(d) + margin) if along >= 0 else min_off
        offset = max(min_off, min(max_off, offset))
        out.append(p_arr + offset * n)
    return out


def offset_normal(shape_name, points, offset):
    if abs(offset) < 1e-12:
        return [np.asarray(p, dtype=float) for p in points]

    fn = get_mesh_fn(shape_name)
    if fn is None:
        return [np.asarray(p, dtype=float) for p in points]

    out = []
    for p in points:
        p_arr = np.asarray(p, dtype=float)
        try:
            _, nv = _closest_point_normal(fn, p_arr)
        except Exception:
            out.append(p_arr)
            continue
        ln = np.linalg.norm(nv)
        if ln < 1e-10:
            out.append(p_arr)
        else:
            out.append(p_arr + offset * (nv / ln))
    return out


# ============================================================================
# 周期曲线构造
# ============================================================================

def ep_to_cv(ep_list):
    n = len(ep_list)
    if n < 4:
        return None
    pts = np.array(ep_list, dtype=float)
    p_hat = np.fft.fft(pts, axis=0)
    k = np.arange(n)
    eig = 4.0 + 2.0 * np.cos(2.0 * np.pi * k / n)
    return np.fft.ifft(6.0 * p_hat / eig[:, np.newaxis], axis=0).real.tolist()


def create_periodic_curve(ep_list, name):
    n = len(ep_list)
    if n < 4:
        return None

    cvs = ep_to_cv(ep_list)
    if cvs is not None:
        degree = 3
        full = cvs + cvs[:degree]
        knots = list(range(-(degree - 1), n + degree))
        if cmds.objExists(name):
            cmds.delete(name)
        try:
            crv = cmds.curve(d=degree, p=full, k=knots, per=True, name=name)
            if cmds.getAttr(f"{crv}.form") == 2:
                return crv
        except Exception:
            pass

    if cmds.objExists(name):
        cmds.delete(name)
    crv = cmds.curve(d=3, ep=ep_list, name=name)
    try:
        cmds.closeCurve(crv, ch=False, rpo=True, bb=0.1, bki=False, bl=False, ps=1)
    except Exception:
        pass
    return crv


# ============================================================================
# 对外主函数
# ============================================================================

def build_dense_laplacian(shape_name, points, closed,
                          samples_per_segment=60, strength="strong",
                          rest_surface=None):
    p = STRENGTH_PRESETS.get(strength, STRENGTH_PRESETS["strong"])
    skeleton, anchors = make_skeleton(points, closed, samples_per_segment)
    if skeleton is None:
        return None, None

    if rest_surface is not None:
        # ★ 新路径：MFnMesh.getClosestPoint 精确投影 → laplacian → 精确再投影
        skeleton_arr = np.array(skeleton, dtype=float)
        proj = rest_surface.project_to_surface(skeleton_arr)
        if p["lap_iter"] > 0 and p["lap_w"] > 0:
            proj = np.array(laplacian(proj, anchors, p["lap_iter"], p["lap_w"]))
            proj = rest_surface.project_to_surface(proj)
        return [np.asarray(row, dtype=float) for row in proj], anchors

    # 旧路径（保底）
    proj = soft_project(shape_name, skeleton, anchors, p["adhesion"])
    return laplacian(proj, anchors, p["lap_iter"], p["lap_w"]), anchors


def build_dense_gaussian(shape_name, points, closed,
                         samples_per_segment=60, strength="strong",
                         rest_surface=None):
    p = STRENGTH_PRESETS.get(strength, STRENGTH_PRESETS["strong"])
    skeleton, anchors = make_skeleton(points, closed, samples_per_segment)
    if skeleton is None:
        return None, None

    skeleton_arr = np.array(skeleton, dtype=float)

    if rest_surface is not None:
        # ★ 新路径：精确投影
        rest_pts = rest_surface.project_to_surface(skeleton_arr)
    else:
        # 旧路径：worldMesh 最近点
        fn = get_mesh_fn(shape_name)
        rest_pts = np.array(skeleton_arr, dtype=float)
        if fn is not None:
            for i, pt in enumerate(skeleton_arr):
                try:
                    rest_pts[i] = _closest_point(fn, pt)
                except Exception:
                    pass

    deltas = rest_pts - skeleton_arr
    for i in anchors:
        if 0 <= i < len(deltas):
            deltas[i] = 0.0

    filtered = gaussian(deltas.tolist(), anchors,
                        p["gauss_sigma"], p["gauss_r"], closed)
    final = []
    for i, pt in enumerate(skeleton_arr):
        if i in anchors:
            final.append(list(pt))
        else:
            final.append((pt + p["adhesion"] * np.asarray(filtered[i], dtype=float)).tolist())
    return final, anchors
    

def refine_points_along_surface(shape_name, points, closed,
                                samples_per_segment=60,
                                internal_per_segment=3,
                                strength="strong",
                                rest_surface=None):
    dense, anchors = build_dense_laplacian(
        shape_name, points, closed, samples_per_segment, strength,
        rest_surface=rest_surface)
    if dense is None:
        return None
    return simplify_by_anchors(dense, anchors, internal_per_segment)