# -*- coding: utf-8 -*-
"""Projection/cloth numeric profiles; original tolerances and formulas retained."""
import numpy as np


_NATIVE_BACKEND = None
_NATIVE_ENABLED = False
_NATIVE_ERROR = None


def get_native_status():
    """Read-only backend status; importing this module never writes a scene."""
    backend = globals().get('_NATIVE_BACKEND')
    return dict(loaded=backend is not None,
                enabled=bool(backend is not None and globals().get('_NATIVE_ENABLED',False)),
                path=getattr(backend,'__file__',None),
                error=globals().get('_NATIVE_ERROR'),
                api_version=getattr(backend,'BACKEND_API_VERSION',None))


def set_native_enabled(enabled=True):
    global _NATIVE_ENABLED
    _NATIVE_ENABLED = bool(enabled and globals().get('_NATIVE_BACKEND') is not None)
    return _NATIVE_ENABLED


def _native_failure(exc):
    global _NATIVE_ENABLED, _NATIVE_ERROR
    _NATIVE_ENABLED = False
    _NATIVE_ERROR = '{}: {}'.format(type(exc).__name__,exc)
    import warnings
    warnings.warn('weightCurveTool native profiles disabled; using Python: '+_NATIVE_ERROR,RuntimeWarning)


def _native_inputs(values,xs,ys,radius,dimensions):
    if globals().get('_NATIVE_BACKEND') is None or not globals().get('_NATIVE_ENABLED',False):
        return None
    try:
        # Preserve the original NumPy scalar arithmetic for float32/complex
        # callers; the accelerated contract is binary64/Python-float input.
        raw=[np.asarray(a) for a in (values,xs,ys)]
        if any(a.dtype.kind not in 'biuf' for a in raw):
            return None
        if any(a.dtype.kind in 'fc' and a.dtype!=np.dtype(np.float64) for a in raw):
            return None
        if isinstance(radius,np.floating) and radius.dtype!=np.dtype(np.float64):
            return None
        data=np.ascontiguousarray(raw[0],dtype=np.float64)
        x=np.ascontiguousarray(xs,dtype=np.float64)
        y=np.ascontiguousarray(ys,dtype=np.float64)
        r=float(radius)
        if (data.ndim!=dimensions or x.shape!=(4,) or y.shape!=(4,)
                or not np.isfinite(r) or r==0.
                or not all(np.all(np.isfinite(a)) for a in (data,x,y))):
            return None
        return data,x,y,r
    except (TypeError,ValueError,OverflowError):
        return None


def _native_profile(values,xs,ys,radius,offset):
    args=_native_inputs(values,xs,ys,radius,1)
    if args is None:return None
    try:
        return _NATIVE_BACKEND.profile_batch(*args,offset)
    except Exception as exc:
        _native_failure(exc)
        return None


def _native_split(matrix,budget,xs,ys,radius):
    args=_native_inputs(matrix,xs,ys,radius,2)
    if args is None:return None
    try:
        cap=np.ascontiguousarray(budget,dtype=np.float64)
        if cap.shape!=(args[0].shape[1],) or not np.all(np.isfinite(cap)):return None
    except (TypeError,ValueError,OverflowError):
        return None
    try:
        return _NATIVE_BACKEND.split_batch(args[0],cap,args[1],args[2],args[3])
    except Exception as exc:
        _native_failure(exc)
        return None

def bezier_v(p, t):
    omt = 1.0 - t
    return (p[0] * omt ** 3 + 3.0 * p[1] * t * omt ** 2
            + 3.0 * p[2] * t ** 2 * omt + p[3] * t ** 3)



def bezier_t(p, v, tol=1e-4, max_iter=48):
    lo, hi = 0.0, 1.0
    for _ in range(max_iter):
        mid = (lo + hi) * 0.5
        err = bezier_v(p, mid) - v
        if err > tol:
            hi = mid
        elif err < -tol:
            lo = mid
        else:
            return mid
    return (lo + hi) * 0.5



def get_weight(x, xs, ys):
    if x <= 0.0:
        return float(ys[0])
    if x >= 1.0:
        return float(ys[3])
    return bezier_v(ys, bezier_t(xs, x))



def _ik_x(v, p):
    d = v[0] * v[0] + v[1] * v[1] + v[2] * v[2]
    if d < 1e-12:
        return 0.0
    return (v[0] * p[0] + v[1] * p[1] + v[2] * p[2]) / d



def _ik_xs(v, p, ps):
    x = _ik_x(v, p)
    return [_ik_x(v, pt) - x for pt in ps]



def _ik_weights(wxs, xs, ys, r):
    native=_native_profile(wxs,xs,ys,r,.5)
    if native is not None:return native.tolist()
    return [get_weight(x / r + 0.5, xs, ys) for x in wxs]



def _soft_weights(wxs, xs, ys, r):
    native=_native_profile(wxs,xs,ys,r,0.)
    if native is not None:return native.tolist()
    return [get_weight(x / r, xs, ys) for x in wxs]



def _split_weights(wx_matrix, max_weights, xs, ys, r):
    """累积 FK 权重。

    语义（n_joints = n_layers + 1）：
        joint0       = 1 - w_0           （链头纯色，第 1 段完整剩余）
        joint_i      = w_{i-1} - w_i     （1 <= i <= n-2，中间关节，相邻段过渡）
        joint_{n-1}  = w_{n-2}           （链尾纯色）

    其中 w_i = get_weight(wxs_i / r + 0.5, xs, ys)：
        · w_i(joint_i)     ≈ 0    （p_i = joint_i 前一个关节，wxs = -|d|）
        · w_i(joint_{i+1}) = 0.5  （p_i = joint_{i+1}，wxs = 0）
        · w_i(更远)        ≈ 1    （wxs > |d|，clip 到 1）

    每行权重和 = 1（乘 max_weights 前）。
    """
    n_layers = len(wx_matrix)
    if n_layers == 0:
        return []
    native=_native_split(wx_matrix,max_weights,xs,ys,r)
    if native is not None:return native.reshape(-1).tolist()
    n_vtx = len(wx_matrix[0])
    n_joints = n_layers + 1

    W = []
    for wxs in wx_matrix:
        w = [get_weight(x / r + 0.5, xs, ys) for x in wxs]
        W.append(np.asarray(w, dtype=float))

    out = np.zeros((n_vtx, n_joints), dtype=float)
    prev = np.ones(n_vtx, dtype=float)
    for j in range(n_layers):
        out[:, j] = prev - W[j]
        prev = W[j]
    out[:, n_joints - 1] = prev

    out *= np.asarray(max_weights, dtype=float)[:, None]
    return out.reshape(-1).tolist()






def _cloth_point_to_chain_dist_t(points, chain_positions):
    """点到多段折线的最短距离 + 归一化弧长参数 t。"""
    points = np.atleast_2d(np.asarray(points, dtype=np.float64))
    K = len(chain_positions)
    if K == 1:
        delta = points - chain_positions[0][None, :]
        return np.linalg.norm(delta, axis=1), np.zeros(len(points))

    starts = chain_positions[:-1]
    ends = chain_positions[1:]
    lengths = np.linalg.norm(ends - starts, axis=1)
    cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
    total = max(float(cumulative[-1]), 1e-8)

    best_dist_sq = np.full(len(points), np.inf)
    best_arc = np.zeros(len(points))

    for si in range(K - 1):
        a = starts[si]
        b = ends[si]
        ab = b - a
        denom = float(np.dot(ab, ab))
        if denom < 1e-12:
            continue
        ap = points - a[None, :]
        u = np.clip(np.sum(ap * ab[None, :], axis=1) / denom, 0.0, 1.0)
        closest = a[None, :] + u[:, None] * ab[None, :]
        delta = points - closest
        dist_sq = np.sum(delta * delta, axis=1)
        better = dist_sq < best_dist_sq
        best_dist_sq[better] = dist_sq[better]
        best_arc[better] = cumulative[si] + u[better] * lengths[si]

    return np.sqrt(best_dist_sq), np.clip(best_arc / total, 0.0, 1.0)



def _cloth_sample_chain_positions(chain_positions, sample_t):
    positions = np.asarray(chain_positions, dtype=np.float64)
    K = len(positions)
    if K == 1:
        return np.repeat(positions[:1], len(sample_t), axis=0)
    lengths = np.linalg.norm(positions[1:] - positions[:-1], axis=1)
    cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
    total = max(float(cumulative[-1]), 1e-8)
    target = np.asarray(sample_t, dtype=np.float64) * total
    seg_idx = np.searchsorted(cumulative[1:], target, side="right")
    seg_idx = np.clip(seg_idx, 0, K - 2)
    local = (target - cumulative[seg_idx]) / np.maximum(
        lengths[seg_idx], 1e-8)
    return (positions[seg_idx] +
            (positions[seg_idx + 1] - positions[seg_idx]) * local[:, None])



def _cloth_build_local_radius_curves(all_chain_pos, sample_count=129):
    sample_t = np.linspace(0.0, 1.0, int(sample_count))
    sampled = [_cloth_sample_chain_positions(cp, sample_t)
               for cp in all_chain_pos]
    n = len(sampled)
    curves = []
    for i in range(n):
        prev_p = sampled[(i - 1) % n]
        next_p = sampled[(i + 1) % n]
        d_prev = np.linalg.norm(sampled[i] - prev_p, axis=1)
        d_next = np.linalg.norm(sampled[i] - next_p, axis=1)
        curves.append(np.maximum(0.5 * np.minimum(d_prev, d_next), 1e-8))
    return sample_t, curves



def _cloth_evaluate_local_radius(nearest_chain, nearest_t, sample_t, curves):
    result = np.empty(len(nearest_chain), dtype=np.float64)
    for i, curve in enumerate(curves):
        mask = nearest_chain == i
        if np.any(mask):
            result[mask] = np.interp(nearest_t[mask], sample_t, curve)
    return result



def _cloth_bezier_eval(x, xs, ys, iterations=36):
    """向量化 Bezier 求值：xs 反解 t，用 t 算 ys。"""
    x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
    lo = np.zeros_like(x)
    hi = np.ones_like(x)
    xs = tuple(float(v) for v in xs)
    ys = tuple(float(v) for v in ys)
    for _ in range(iterations):
        mid = (lo + hi) * 0.5
        omt = 1.0 - mid
        val = (xs[0] * omt ** 3 + 3.0 * xs[1] * mid * omt ** 2
               + 3.0 * xs[2] * mid ** 2 * omt + xs[3] * mid ** 3)
        lo = np.where(val < x, mid, lo)
        hi = np.where(val >= x, mid, hi)
    t = (lo + hi) * 0.5
    omt = 1.0 - t
    return (ys[0] * omt ** 3 + 3.0 * ys[1] * t * omt ** 2
            + 3.0 * ys[2] * t ** 2 * omt + ys[3] * t ** 3)



def _cloth_calc_main_chain_mix(points, all_chain_pos, soft_radius,
                                strategy='soft_direct', expand_factor=1.0):
    """4 主链自然衰减，返回 mix (N, K)。无 mask、无 anchor，纯自然衰减。"""
    points = np.asarray(points, dtype=np.float64)
    K = len(all_chain_pos)
    N = len(points)

    dist_and_t = [_cloth_point_to_chain_dist_t(points, cp)
                  for cp in all_chain_pos]
    distances = np.column_stack([x[0] for x in dist_and_t])
    all_t = np.column_stack([x[1] for x in dist_and_t])

    nearest_chain = np.argmin(distances, axis=1)
    nearest_t = all_t[np.arange(N), nearest_chain]

    sample_t, curves = _cloth_build_local_radius_curves(all_chain_pos)
    local_radius = _cloth_evaluate_local_radius(
        nearest_chain, nearest_t, sample_t, curves)

    span = 2.0 * local_radius
    if strategy == 'soft_direct':
        denom = np.maximum(span, float(soft_radius))
    elif strategy in ('factor', 'fixed'):
        denom = np.maximum(span * float(expand_factor), 1e-8)
    else:
        raise RuntimeError("CLOTH_MAIN_STRATEGY 无效：{}".format(strategy))
    denom = np.maximum(denom, 1e-8)

    scores = np.zeros_like(distances)
    for j in range(K):
        scores[:, j] = np.maximum(0.0, 1.0 - distances[:, j] / denom)

    row_sum = scores.sum(axis=1)
    mix = np.zeros_like(scores)
    valid = row_sum > 1e-9
    if np.any(valid):
        mix[valid] = scores[valid] / row_sum[valid, None]
    bad = ~valid
    if np.any(bad):
        mix[bad] = 0.0
        mix[np.flatnonzero(bad), nearest_chain[bad]] = 1.0

    return mix, distances



def _cloth_fk_chain_weights(points, chain_positions, xs, ys_fk, fk_radius):
    """链内 FK 累积权重（同 spine 的 p=joint2 语义）。

    返回 (N, chain_len)，每行归一化到 1。
    ys_fk 必须是 UI 原始语义 [0,0,1,1]（paint 收到的 ys 是翻转值）。
    """
    points = np.atleast_2d(np.asarray(points, dtype=np.float64))
    chain_positions = np.asarray(chain_positions, dtype=np.float64)
    K = len(chain_positions)
    N = len(points)
    if K == 1:
        return np.ones((N, 1))
    result = np.zeros((N, K))
    previous = np.ones(N)
    for seg_i in range(K - 1):
        a = chain_positions[seg_i]
        b = chain_positions[seg_i + 1]
        v = b - a
        ln = float(np.linalg.norm(v))
        if ln < 1e-9:
            result[:, seg_i] = np.maximum(previous, 0.0)
            continue
        v = v / ln
        wxs = (points - b[None, :]) @ v
        x = wxs / fk_radius + 0.5
        current = np.clip(_cloth_bezier_eval(x, xs, ys_fk), 0.0, 1.0)
        result[:, seg_i] = np.maximum(previous - current, 0.0)
        previous = current
    result[:, -1] = np.maximum(previous, 0.0)

    sums = result.sum(axis=1)
    invalid = sums <= 1e-9
    valid = ~invalid
    if np.any(valid):
        result[valid] /= sums[valid, None]
    if np.any(invalid):
        bad_rows = np.flatnonzero(invalid)
        delta = points[invalid][:, None, :] - chain_positions[None, :, :]
        nearest = np.argmin(np.sum(delta * delta, axis=2), axis=1)
        result[bad_rows, :] = 0.0
        result[bad_rows, nearest] = 1.0
    return result



# Optional binary extension: absence/ABI/dependency failures preserve Python.
try:
    from . import _wct_profiles_native as _candidate_backend
    if getattr(_candidate_backend,'BACKEND_API_VERSION',None)!=1:
        raise ImportError('Unsupported native profiles API')
    _probe=np.array([-1.,-.25,0.,.25,1.],dtype=np.float64)
    _probe_x=np.array([0.,.5,.5,1.],dtype=np.float64)
    _probe_y=np.array([0.,0.,1.,1.],dtype=np.float64)
    _probe_result=_candidate_backend.profile_batch(_probe,_probe_x,_probe_y,1.,.5)
    _probe_reference=[get_weight(v+.5,_probe_x.tolist(),_probe_y.tolist()) for v in _probe]
    if not np.allclose(_probe_result,_probe_reference,atol=1e-12,rtol=1e-12):
        raise ImportError('Native profiles startup parity check failed')
    _NATIVE_BACKEND=_candidate_backend
    _NATIVE_ENABLED=True
except Exception as _load_error:
    _NATIVE_BACKEND=None
    _NATIVE_ENABLED=False
    _NATIVE_ERROR='{}: {}'.format(type(_load_error).__name__,_load_error)
