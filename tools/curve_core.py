# -*- coding: utf-8 -*-
"""curve_core —— 曲线参数化 / 平滑 / 重采样工具

与 surfaceWeight/curve_core.py 一致，是 RestPoseSurface 的底层曲线工具集。
与 tools/CurveRefine.py 独立（后者是旧的空间插值工具，P2 之后逐步被替代）。
"""
import numpy as np
from scipy import interpolate


def cubic_spline(points, close=False, count=8):
    if close:
        points = np.concatenate([points, points[0][None]], axis=0)
    x = np.linspace(0, 1, len(points))
    if close:
        interp = interpolate.CubicSpline(x, points, bc_type='periodic')
    else:
        interp = interpolate.CubicSpline(x, points)
    x_new = np.linspace(0, 1, (len(points) - 1) * count + 1)
    if close:
        x_new = x_new[:-1]
    return interp(x_new)


def do_boor(knots, i, degree, u):
    if degree == 0:
        if knots[i] < u <= knots[i + 1]:
            return 1.0
        elif u == knots[0] and knots[i] <= u <= knots[i + 1]:
            return 1.0
        return 0.0
    u0 = u - knots[i]
    scale = knots[i + degree] - knots[i]
    u0 = u0 / scale if scale else u0
    b0 = do_boor(knots, i, degree - 1, u)
    u1 = knots[i + degree + 1] - u
    scale = knots[i + degree + 1] - knots[i + 1]
    u1 = u1 / scale if scale else u1
    b1 = do_boor(knots, i + 1, degree - 1, u)
    return u0 * b0 + u1 * b1


def get_distance(v1, v2):
    return sum([(e1 - e2) ** 2 for e1, e2 in zip(v1, v2)]) ** 0.5


def get_parameter_by_points(points, close=False):
    if close:
        points = list(points) + [points[0]]
    distances = [get_distance(p1, p2) for p1, p2 in zip(points, points[1:])]
    s = sum(distances)
    us = [sum(distances[:i]) / s for i in range(len(points))]
    if close:
        us.pop(-1)
    return us


def get_curve_parameter_list(count, close=False):
    if isinstance(count, list):
        if isinstance(count[0], list):
            return get_parameter_by_points(count, close)
        return count
    if count == 0:
        return []
    if count == 1:
        return [0.5]
    step = 1.0 / count if close else 1.0 / (count - 1)
    return [step * i for i in range(count)]


def get_uniform_knots(count, degree):
    if degree == 1:
        step = 1.0 / (count - 3)
        return [(i - 2) * step for i in range(count + 2)]
    if degree == 2:
        step = 1.0 / (count - 3)
        return [(i - 2.5) * step for i in range(count + 3)]
    step = 1.0 / (count - 3)
    return [(i - 3) * step for i in range(count + 4)]


def get_uniform_close_spline_weights(cn, jn, degree):
    us = get_curve_parameter_list(jn, True)
    cn += 3
    knots = get_uniform_knots(cn, degree)
    ws = [[do_boor(knots, ci, degree, u) for u in us] for ci in range(cn)]
    for ji in range(len(us)):
        ws[1][ji] += ws[-2][ji]
        ws[2][ji] += ws[-1][ji]
        ws[-3][ji] += ws[0][ji]
    return ws[1:-2]


def get_uniform_spline_weights(cn, jn, degree):
    us = get_curve_parameter_list(jn, False)
    cn += 2
    knots = get_uniform_knots(cn, degree)
    ws = [[do_boor(knots, ci, degree, u) for u in us] for ci in range(cn)]
    for ji in range(len(us)):
        ws[1][ji] += 2 * ws[0][ji]
        ws[2][ji] -= ws[0][ji]
        ws[-2][ji] += 2 * ws[-1][ji]
        ws[-3][ji] -= ws[-1][ji]
    return ws[1:-1]


def get_follow_weights(cn, jn, close=False):
    if jn == 1:
        weights = get_follow_weights(cn, 3, close)
        return [[1.0 if w[1] >= 0.6 else 0.0] for w in weights]
    cs = get_curve_parameter_list(cn, close)
    js = get_curve_parameter_list(jn, close)
    jn = len(js)
    if close:
        js.append(1.0)
    weights = []
    for c in cs:
        weights.append([0] * jn)
        for i, (v1, v2) in enumerate(zip(js, js[1:])):
            if v1 <= c <= v2:
                s = v2 - v1
                weights[-1][(i + 1) % jn] = (c - v1) / s
                weights[-1][i] = (v2 - c) / s
    return weights


def smooth_cubic_spline(points, close=False, count=8):
    if close:
        weights = get_uniform_close_spline_weights(len(points), count, 3)
    else:
        weights = get_uniform_spline_weights(len(points), count, 3)
    return np.array(weights).T @ points


def re_curve(points, count=8, close=False):
    weights = np.array(get_follow_weights(count, points.tolist(), close=close))
    return weights @ points


def gaussian_kernel(window_length, sigma=None):
    if window_length % 2 == 0:
        raise ValueError("窗口长度必须是奇数")
    if sigma is None:
        sigma = 0.3 * ((window_length - 1) * 0.5 - 1) + 0.8
    x = np.linspace(-(window_length - 1) / 2, (window_length - 1) / 2, window_length)
    kernel = np.exp(-x ** 2 / (2 * sigma ** 2))
    return kernel / np.sum(kernel)


def smooth_with_gaussian(values, window_length=1, sigma=1.0):
    wl = 1 + window_length * 2
    padded = np.pad(values, wl, mode='reflect')
    padded[:wl] = 2 * values[0] - padded[:wl]
    padded[-wl:] = 2 * values[-1] - padded[-wl:]
    kernel = gaussian_kernel(wl, sigma)
    smoothed = np.convolve(padded, kernel, mode='same')
    return smoothed[wl:-wl]


def smooth_curve_distance(points, close=True, sigma=2):
    us = get_parameter_by_points(points, close=close)
    if close:
        us.insert(0, us[-1] - 1.0)
        us.append(1.0)
    else:
        us.insert(0, us[0])
        us.append(us[-1])
    us = smooth_with_gaussian(np.array(us), sigma=sigma)[1:-1]
    if us[0] < 0:
        us[0] = 1 + us[0]
    return re_curve(points, us.tolist(), close=close)