# -*- coding: utf-8 -*-
"""geodesic —— 网格表面测地距离

优先加载 geodesic.dll（igl 的 exact_geodesic + Dijkstra fast），
找不到 DLL 时 fallback 到 scipy 的 csgraph.dijkstra。

DLL 位置：weightCurveTool/plug-ins/geodesic.dll
  · plugin_loader.load_all_plugins_in_folder 会扫该目录下的 .mll
  · .dll 需要手动放入（当前没有的话走 scipy fallback，功能正确但慢）
"""
import os

import numpy as np

try:
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

from ..Logger import log


_DLL_PATH_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), '..', 'plug-ins', 'geodesic.dll'),
    os.path.join(os.path.dirname(__file__), '..', 'libs', 'geodesic.dll'),
]

_lib = None
_DLL_ERROR = None


def _load_dll():
    global _lib, _DLL_ERROR
    if _lib is not None:
        return _lib
    if _DLL_ERROR is not None:
        return None
    try:
        from ctypes import CDLL, c_double, c_int, POINTER
    except ImportError as e:
        _DLL_ERROR = "ctypes 不可用: {}".format(e)
        return None

    for path in _DLL_PATH_CANDIDATES:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            continue
        try:
            lib = CDLL(path)
            lib.exact_geodesic_all.argtypes = [
                POINTER(c_double), POINTER(c_int), POINTER(c_double), c_int, c_int]
            lib.exact_geodesic_all.restype = None
            lib.fast_geodesic_all.argtypes = [
                POINTER(c_double), POINTER(c_int), POINTER(c_double), c_int, c_int]
            lib.fast_geodesic_all.restype = None
            _lib = lib
            log.info("[geodesic] 加载 DLL: {}".format(path))
            return _lib
        except Exception as e:
            log.warn("[geodesic] 加载失败 {}: {}".format(path, e))

    _DLL_ERROR = "找不到 geodesic.dll"
    log.info("[geodesic] 未找到 DLL，使用 scipy Dijkstra fallback")
    return None


def _build_graph(vertices, faces):
    if not _HAS_SCIPY:
        return None
    n = len(vertices)
    rows, cols, data = [], [], []
    seen = set()
    for face in faces:
        for i in range(3):
            a = int(face[i])
            b = int(face[(i + 1) % 3])
            key = (min(a, b), max(a, b))
            if key in seen:
                continue
            seen.add(key)
            d = float(np.linalg.norm(vertices[a] - vertices[b]))
            rows.append(a); cols.append(b); data.append(d)
            rows.append(b); cols.append(a); data.append(d)
    return csr_matrix((data, (rows, cols)), shape=(n, n))


def get_scipy_distance_matrix(vertices, faces):
    """纯 scipy 多源 Dijkstra（无 DLL 时用）。N 顶点约 N * (E log V)。"""
    if not _HAS_SCIPY:
        raise ImportError("scipy 不可用")
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=np.int64)
    return dijkstra(_build_graph(vertices, faces), directed=False, indices=None)


def _dll_double_arr(lis):
    from ctypes import c_double
    arr = (c_double * len(lis))()
    arr[:] = lis
    return arr


def _dll_int_arr(lis):
    from ctypes import c_int
    arr = (c_int * len(lis))()
    arr[:] = lis
    return arr


def get_distance_matrix(vertices, faces):
    """精确测地距离。DLL 优先，否则 scipy fallback。"""
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=np.int64)
    n = len(vertices)
    lib = _load_dll()
    if lib is None:
        return get_scipy_distance_matrix(vertices, faces)
    from ctypes import c_double, c_int
    V = vertices.T.reshape(-1).tolist()
    F = faces.T.reshape(-1).tolist()
    D = (c_double * (n * n))()
    lib.exact_geodesic_all(_dll_double_arr(V), _dll_int_arr(F), D, c_int(n), c_int(len(faces)))
    return np.frombuffer(D, np.float64).reshape(n, n)


def get_fast_distance_matrix(vertices, faces):
    """快速测地距离。DLL 优先，否则 scipy fallback。"""
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=np.int64)
    n = len(vertices)
    lib = _load_dll()
    if lib is None:
        return get_scipy_distance_matrix(vertices, faces)
    from ctypes import c_double, c_int
    V = vertices.T.reshape(-1).tolist()
    F = faces.T.reshape(-1).tolist()
    D = (c_double * (n * n))()
    lib.fast_geodesic_all(_dll_double_arr(V), _dll_int_arr(F), D, c_int(n), c_int(len(faces)))
    return np.frombuffer(D, np.float64).reshape(n, n)