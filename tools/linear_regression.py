# -*- coding: utf-8 -*-
"""linear_regression —— 三角形重心坐标求解

给定一批三角形 (N, D, 3) + 一批目标点 (N, 3)，求每个目标点在对应三角形上的重心权重。
迭代：w = 沿顶点方向投影到当前估计位置，4 次迭代收敛到足够精度。

注：D 通常是 3（三角形），代码兼容 D > 3。
"""
import numpy as np


def normal_weights(weights):
    weights[np.sum(weights, axis=1) < 1e-5] = 1.0
    weights /= np.sum(weights, axis=1, keepdims=True)
    return weights


def lr_solve_weights(points, point):
    """批量求点落在每个三角形上的重心权重。

    参数：
        points: (N, D, 3)，N 个三角形，每个 D 个顶点，每个顶点 3 维
        point:  (N, 3)，N 个目标点
    返回：
        (N, D)，每行和为 1，所有分量在 [0, 1]
    """
    weights = np.full((points.shape[0], points.shape[1]), 1.0 / points.shape[1])
    n = weights.shape[-1]
    one_weights = np.eye(n)
    for _ in range(4):
        for i in range(n):
            weights[:, i] = 0
            weights = normal_weights(weights)
            zero_point = np.sum(points * weights[:, :, None], axis=1)
            v = points[:, i] - zero_point
            p = point - zero_point
            dot_v = (v * v).sum(-1)
            zero_dot = dot_v < 1e-6
            dot_v[zero_dot] = 1.0
            w = (v * p).sum(-1) / dot_v
            w[zero_dot] = 0.0
            w = np.clip(w, 0, 1)[:, None]
            weights = weights * (1 - w) + one_weights[i][None] * w
    weights = normal_weights(weights)
    return weights