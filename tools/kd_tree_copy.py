# -*- coding: utf-8 -*-
"""kd_tree_copy —— 任意点重定位到 mesh 表面

核心能力：
  · copy_data(points, data)      —— 点重定位到表面，用相同重心权重对 data 插值
  · iter_curve(points, close, count) —— 投影 → 样条加密 → 投影 ... 贴面曲线
  · distances(points)            —— 点到 mesh 的距离

data 可以是：
  · None（默认）—— 用 self.vertices
  · self.embeddings —— 8D MDS 坐标
  · 任意 per-vertex 数组
"""
import numpy as np
from scipy.spatial import cKDTree

from .linear_regression import lr_solve_weights
from .curve_core import cubic_spline


def get_vf(faces):
    vf = {}
    for f, vs in enumerate(faces.tolist()):
        for v in vs:
            vf.setdefault(v, []).append(f)
    return vf


class KDTreeCopy(object):

    def __init__(self, vertices, faces):
        self.vertices = np.asarray(vertices, dtype=float)
        self.faces = np.asarray(faces, dtype=np.int64)
        self.face_count, self.face_dim = self.faces.shape
        self.vtx_count, self.vtx_dim = self.vertices.shape
        self.vf = get_vf(self.faces)
        self.update_ff()
        self.tree = cKDTree(self.vertices)
        self.face_vtx_count = self.faces.shape[1]

    def update_ff(self):
        """扩展 2 环邻接面：每个顶点的邻接面 → 邻接面的邻接面。"""
        ff = {}
        for f, vs in enumerate(self.faces.tolist()):
            for v in vs:
                ff.setdefault(f, set()).update(self.vf[v])
        for v, fs in self.vf.items():
            new_fs = set()
            for f in fs:
                new_fs.update(ff[f])
            self.vf[v] = list(new_fs)

    def get_face_weights(self, points):
        near_vtx = self.tree.query(points)[1]
        counts = [0]
        for v in near_vtx:
            counts.append(counts[-1] + len(self.vf[v]))
        point_count = counts[-1]
        lr_points = np.zeros((point_count, self.face_dim, self.vtx_dim))
        lr_point = np.zeros((point_count, self.vtx_dim))
        for i1, i2, v, p in zip(counts, counts[1:], near_vtx, points):
            lr_points[i1:i2] = self.vertices[self.faces[self.vf[v]]]
            lr_point[i1:i2] = p[None]
        weights = lr_solve_weights(lr_points, lr_point)
        distances = np.linalg.norm(
            (lr_points * weights[:, :, None]).sum(axis=1) - lr_point, axis=1)

        near_faces = []
        near_weights = np.zeros((points.shape[0], self.face_dim))
        for i1, i2, v, i in zip(counts[:-1], counts[1:], near_vtx, range(point_count)):
            fi = np.argmin(distances[i1:i2])
            near_faces.append(self.vf[v][fi])
            near_weights[i] = weights[i1:i2][fi]
        return np.array(near_faces), near_weights

    def copy_data(self, points, data=None):
        if data is None:
            data = self.vertices
        faces, weights = self.get_face_weights(np.asarray(points, dtype=float))
        return (data[self.faces[faces]] * weights[:, :, None]).sum(axis=1)

    def distances(self, points):
        near_points = self.copy_data(points)
        return np.linalg.norm(near_points - np.asarray(points), axis=1)

    def iter_curve(self, points, close=True, count=2):
        points = self.copy_data(np.asarray(points, dtype=float))
        for _ in range(count):
            points = cubic_spline(points, close=close, count=2)
            points = self.copy_data(points)
        return points