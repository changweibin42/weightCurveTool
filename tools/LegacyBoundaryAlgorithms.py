# -*- coding: utf-8 -*-
"""Compatibility methods for retired boundary approaches.

Current strict/Bezier and fallback entry points remain in WeightCalculator.
Methods are retained for existing scripts; no algorithm was removed.
"""
import hashlib
from collections import defaultdict
import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om
from ..Logger import log
from .SurfaceRegion import solve_surface_band, RegionError
try:
    from scipy.spatial import cKDTree
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

class LegacyBoundaryAlgorithmsMixin(object):
    def _sample_rest_spline(self, points, closed, median_edge):
        """Rebuild the same EP NURBS as DualCurveBuilder, without display offset.

        Temporary construction is deleted in finally. We sample the actual
        spline, rather than fitting a second, different Hermite curve to EPs.
        """
        from . import CurveRefine as CR
        pts = np.asarray(points, dtype=float)
        if not np.all(np.isfinite(pts)):
            raise RegionError("曲线包含无效坐标")
        if closed and np.linalg.norm(pts[0] - pts[-1]) < 1e-9:
            pts = pts[:-1]
        temp = None
        undo_enabled = bool(cmds.undoInfo(q=True, state=True))
        try:
            cmds.undoInfo(stateWithoutFlush=False)
            if closed and len(pts) >= 4:
                temp = CR.create_periodic_curve(pts.tolist(), "__wct_rest_sample#")
            else:
                temp = cmds.curve(d=min(3, len(pts) - 1), ep=pts.tolist(),
                                  name="__wct_rest_sample#")
            if not temp:
                raise RegionError("无法构建静止曲线")
            length = float(cmds.arclen(temp, ch=False))
            spacing = max(float(median_edge) / 6.0, 1e-8)
            count = max(64, len(pts) * 20, int(np.ceil(length / spacing)))
            if count > 50000:
                raise RegionError("曲线采样超过 50000 点，请检查曲线尺度或网格")
            params = np.linspace(0.0, 1.0, count, endpoint=not closed)
            samples = [cmds.pointOnCurve(temp, parameter=float(t), position=True,
                                         turnOnPercentage=True) for t in params]
            return np.asarray(samples, dtype=float)
        finally:
            try:
                if temp and cmds.objExists(temp):
                    cmds.delete(temp)
            finally:
                cmds.undoInfo(stateWithoutFlush=undo_enabled)


    def _ik_fwd_outer_mask(self, rs, dense, closed, median_edge, verbose):
        """Keep V16.9's exterior test, independent of the new band coordinate."""
        n = max(64, min(map(len, dense)))
        r, f, b = [self._resample_curve_n(p, n, closed) for p in dense]
        if closed:
            fm = self._cyclic_dtw_match(r, f, "FWD", median_edge, verbose)
            bm = self._cyclic_dtw_match(r, b, "BWD", median_edge, verbose)
            # DTW failure affects the exterior only; never changes band geometry.
            if fm is None or bm is None:
                raise RegionError("IK 外侧曲线匹配失败，请检查三条曲线方向/形状")
            f, b = fm, bm
        verts = np.asarray(rs.core.vertices)
        if _HAS_SCIPY:
            nearest = np.asarray(cKDTree(r).query(verts)[1], dtype=int)
        else:
            nearest = np.empty(len(verts), dtype=int)
            for start in range(0, len(verts), 1024):
                d2 = ((verts[start:start+1024, None] - r[None]) ** 2).sum(axis=2)
                nearest[start:start+1024] = np.argmin(d2, axis=1)
        axis = f[nearest] - b[nearest]
        length = np.linalg.norm(axis, axis=1)
        unit = axis / np.maximum(length, 1e-8)[:, None]
        old_projection = np.einsum('ij,ij->i', verts - r[nearest], unit)
        fwd_length = np.maximum(np.einsum('ij,ij->i', f[nearest] - r[nearest], unit), 1e-6)
        legacy = (length > max(1e-8, median_edge * 1e-5)) & (old_projection > fwd_length)
        best = np.full(len(verts), np.inf)
        best_seg, best_alpha = np.zeros(len(verts), int), np.zeros(len(verts))
        for off in (-3, -2, -1, 0, 1, 2, 3):
            i = (nearest + off) % n if closed else np.clip(nearest + off, 0, n - 2)
            j = (i + 1) % n
            ab = r[j] - r[i]
            alpha = np.clip(np.einsum('ij,ij->i', verts - r[i], ab)
                            / np.maximum(np.einsum('ij,ij->i', ab, ab), 1e-16), 0, 1)
            delta = verts - (r[i] + alpha[:, None] * ab)
            d2 = np.einsum('ij,ij->i', delta, delta)
            better = d2 < best
            best[better], best_seg[better], best_alpha[better] = d2[better], i[better], alpha[better]
        nxt = (best_seg + 1) % n
        def interp(p):
            return p[best_seg] * (1 - best_alpha[:, None]) + p[nxt] * best_alpha[:, None]
        pr, pf, pb = interp(r), interp(f), interp(b)
        cross = pf - pb
        cross2 = np.einsum('ij,ij->i', cross, cross)
        rq = np.einsum('ij,ij->i', pr - pb, cross) / np.maximum(cross2, 1e-16)
        vq = np.einsum('ij,ij->i', verts - pb, cross) / np.maximum(cross2, 1e-16)
        valid = ((np.sqrt(cross2) > max(1e-8, median_edge * 1e-5))
                 & np.isfinite(rq) & np.isfinite(vq) & (rq >= -.05) & (rq <= 1.05))
        return legacy & valid & (vq > 1.0)


    def _densify_project_v164(self, rs, pts, closed, median_edge):
        from .WeightCalculator import _hermite_chain
        dense = np.array(_hermite_chain(pts.tolist(), closed, 20), dtype=float)
        spacing = median_edge / 3.0
        work = np.vstack([dense, dense[0:1]]) if closed else dense
        diffs = np.diff(work, axis=0)
        seg_lens = np.linalg.norm(diffs, axis=1)
        result = []
        for i in range(len(work) - 1):
            n_sub = max(1, int(np.ceil(seg_lens[i] / spacing)))
            for j in range(n_sub):
                t = j / float(n_sub)
                result.append(work[i] + t * (work[i + 1] - work[i]))
        result = np.asarray(result, dtype=float)
        return rs.project_to_surface(result)


    def _compute_in_band_facegraph(self, mesh_name, rs, ridge_curve,
                                   fwd_curve, bwd_curve, verbose,
                                   already_aligned=False):
        projected = []
        for pts in (ridge_curve, fwd_curve, bwd_curve):
            fids, xyz = self._project_curve_to_faces_with_coords(rs, pts)
            if xyz is None or min(fids) < 0:
                raise RegionError("曲线面投影失败")
            projected.append((xyz, fids))
        return solve_surface_band(rs.core.vertices, rs.core.faces, projected)['mask']


    def _read_curve_ep(self, mesh_name, kind):
        if kind == "main":
            names = ["{}_ring_laplacian".format(mesh_name),
                     "{}_ring_gaussian".format(mesh_name)]
        else:
            names = ["{}_{}_ring_laplacian".format(mesh_name, kind),
                     "{}_{}_ring_gaussian".format(mesh_name, kind)]
        for name in names:
            if not cmds.objExists(name):
                continue
            eps = cmds.ls(name + ".ep[*]", fl=True)
            if eps:
                return np.array([cmds.xform(ep, q=True, ws=True, t=True)
                                 for ep in eps], dtype=float)
            cvs = cmds.ls(name + ".cv[*]", fl=True)
            if cvs:
                return np.array([cmds.xform(cv, q=True, ws=True, t=True)
                                 for cv in cvs], dtype=float)
        return None


    @staticmethod
    def _resample_curve_n(points, n, closed=True):
        arr = np.asarray(points, dtype=float)
        work = np.vstack([arr, arr[0:1]]) if closed else arr
        diffs = np.diff(work, axis=0)
        segs = np.linalg.norm(diffs, axis=1)
        cum = np.concatenate([[0.0], np.cumsum(segs)])
        total = cum[-1]
        if total < 1e-10:
            return arr[:n] if len(arr) >= n else arr
        targets = (np.arange(n) / float(n) * total) if closed \
                  else (np.arange(n) / float(n - 1) * total)
        idxs = np.searchsorted(cum, targets, side='right') - 1
        idxs = np.clip(idxs, 0, len(cum) - 2)
        r = (targets - cum[idxs]) / (cum[idxs + 1] - cum[idxs] + 1e-10)
        return work[idxs] + r[:, None] * (work[idxs + 1] - work[idxs])


    @staticmethod
    def _resample_to_n(points, n_target, closed):
        from .WeightCalculator import WeightCalculator
        return WeightCalculator._resample_curve_n(points, n_target, closed)


    @staticmethod
    def _curve_tangents(pts):
        pts = np.asarray(pts, dtype=float)
        n = len(pts)
        T = np.zeros_like(pts)
        for i in range(n):
            T[i] = pts[(i + 1) % n] - pts[(i - 1) % n]
        lens = np.linalg.norm(T, axis=1, keepdims=True)
        lens[lens < 1e-10] = 1.0
        return T / lens


    @staticmethod
    def _dtw_cost_path(A, B, TA, TB, band, median_edge):
        n = len(A)
        m = len(B)
        dist = np.linalg.norm(A[:, None, :] - B[None, :, :], axis=2)
        dot = np.clip(TA @ TB.T, -1.0, 1.0)
        local_cost = (dist / max(median_edge, 1e-8)
                      + 1.5 * (1.0 - dot))
        dp = np.full((n, m), np.inf, dtype=np.float64)
        parent = np.zeros((n, m), dtype=np.uint8)
        dp[0, 0] = local_cost[0, 0]
        transitions = [(1, 1, 1), (1, 2, 2), (2, 1, 3)]
        for i in range(n):
            j_min = max(0, i - band)
            j_max = min(m, i + band + 1)
            for j in range(j_min, j_max):
                if i == 0 and j == 0:
                    continue
                best = np.inf
                best_code = 0
                for di, dj, code in transitions:
                    pi, pj = i - di, j - dj
                    if pi < 0 or pj < 0:
                        continue
                    if dp[pi, pj] < best:
                        best = dp[pi, pj]
                        best_code = code
                if best_code:
                    dp[i, j] = best + local_cost[i, j]
                    parent[i, j] = best_code
        if not np.isfinite(dp[-1, -1]):
            return None, None
        path = []
        i, j = n - 1, m - 1
        while True:
            path.append((i, j))
            if i == 0 and j == 0:
                break
            code = int(parent[i, j])
            if code == 1:
                i -= 1; j -= 1
            elif code == 2:
                i -= 1; j -= 2
            elif code == 3:
                i -= 2; j -= 1
            else:
                return None, None
        path.reverse()
        return np.asarray(path, dtype=int), float(dp[-1, -1]) / len(path)


    @staticmethod
    def _path_to_mapping(path, n):
        groups = defaultdict(list)
        for i, j in path:
            groups[int(i)].append(float(j))
        known_i = np.array(sorted(groups.keys()), dtype=float)
        known_j = np.array([np.mean(groups[int(i)]) for i in known_i])
        mapping = np.interp(np.arange(n, dtype=float), known_i, known_j)
        mapping = np.maximum.accumulate(mapping)
        mapping = np.clip(mapping, 0.0, n - 1.0)
        return mapping


    def _cyclic_dtw_match(self, ridge, curve, label, median_edge, verbose):
        n = len(ridge)
        if len(curve) != n:
            log.warn("[权重] {} 与 Ridge 采样数不一致 ({} vs {})".format(
                label, len(curve), n))
            return None

        COARSE_N = 64
        COARSE_BAND_RATIO = 0.35
        FINE_BAND_RATIO = 0.25

        K = min(COARSE_N, n)
        A = self._resample_curve_n(ridge, K)
        TA = self._curve_tangents(A)
        best = None
        for direction in (1, -1):
            candidate = curve if direction == 1 else curve[::-1].copy()
            C = self._resample_curve_n(candidate, K)
            TC = self._curve_tangents(C)
            for shift in range(K):
                rotated = np.roll(C, -shift, axis=0)
                rot_T = np.roll(TC, -shift, axis=0)
                path, cost = self._dtw_cost_path(
                    A, rotated, TA, rot_T,
                    band=max(8, int(K * COARSE_BAND_RATIO)),
                    median_edge=median_edge)
                if path is None:
                    continue
                if best is None or cost < best["cost"]:
                    best = {"cost": cost, "shift": shift, "direction": direction}

        if best is None:
            log.warn("[权重] {} 粗匹配失败".format(label))
            return None

        direction = best["direction"]
        base_curve = curve if direction == 1 else curve[::-1].copy()
        base_shift = int(round(best["shift"] * n / float(K))) % n

        stride = max(1, n // K)
        offsets = sorted(set([-max(1, stride // 2), 0, max(1, stride // 2)]))
        TA = self._curve_tangents(ridge)
        best = None
        for offset in offsets:
            shift = (base_shift + offset) % n
            rotated = np.roll(base_curve, -shift, axis=0)
            rot_T = self._curve_tangents(rotated)
            path, cost = self._dtw_cost_path(
                ridge, rotated, TA, rot_T,
                band=max(12, int(n * FINE_BAND_RATIO)),
                median_edge=median_edge)
            if path is None:
                continue
            if best is None or cost < best["cost"]:
                best = {"cost": cost, "shift": shift, "rotated": rotated, "path": path}

        if best is None:
            log.warn("[权重] {} 精匹配失败".format(label))
            return None

        mapping = self._path_to_mapping(best["path"], n)
        x = np.arange(n, dtype=float)
        matched = np.column_stack([
            np.interp(mapping, x, best["rotated"][:, axis])
            for axis in range(3)
        ])

        map_diff = np.diff(mapping)
        monotonic = bool(np.all(map_diff >= -1e-8))
        mapped_T = self._curve_tangents(matched)
        tangent_dot = np.sum(TA * mapped_T, axis=1)
        mean_dot = float(np.mean(tangent_dot))

        if verbose:
            log.info("[权重] DTW {}: shift={}, cost={:.2f}, 单调={}, "
                     "步长=[{:.2f},{:.2f}], 平均切线点积={:.3f}".format(
                         label, best["shift"], best["cost"], monotonic,
                         float(map_diff.min()), float(map_diff.max()), mean_dot))

        if not monotonic:
            log.warn("[权重] DTW {} 非单调".format(label))
            return None
        if mean_dot < 0.5:
            log.warn("[权重] DTW {} 平均切线点积 {:.2f} < 0.5".format(label, mean_dot))
            return None

        return matched     
        
        return matched


    def _build_face_graph(self, mesh_name, rs):
        fingerprint = (
            rs.core.vtx_count, rs.core.face_count,
            hashlib.blake2b(np.ascontiguousarray(rs.core.vertices).tobytes(), digest_size=16).digest(),
            hashlib.blake2b(np.ascontiguousarray(rs.core.faces).tobytes(), digest_size=16).digest())
        cached = self._face_graph_cache.get(mesh_name)
        if cached is not None and cached[0] == fingerprint:
            return (cached[1], cached[2], cached[3], cached[4],
                    cached[5], cached[6], cached[7])

        faces = rs.core.faces
        verts = rs.core.vertices
        n_faces = len(faces)

        edge_to_faces = {}
        edge_lengths = []
        for fid in range(n_faces):
            face = faces[fid]
            for i in range(3):
                a = int(face[i])
                b = int(face[(i + 1) % 3])
                e = (min(a, b), max(a, b))
                edge_to_faces.setdefault(e, []).append(fid)
                edge_lengths.append(float(np.linalg.norm(verts[a] - verts[b])))

        face_adj = [[] for _ in range(n_faces)]
        for e, fs in edge_to_faces.items():
            if len(fs) == 2:
                face_adj[fs[0]].append((fs[1], e))
                face_adj[fs[1]].append((fs[0], e))

        vf_1ring = defaultdict(list)
        for f in range(n_faces):
            for v in faces[f]:
                vf_1ring[int(v)].append(f)

        face_centers = verts[faces].mean(axis=1)

        tri_a = verts[faces[:, 1]] - verts[faces[:, 0]]
        tri_b = verts[faces[:, 2]] - verts[faces[:, 0]]
        face_normals = np.cross(tri_a, tri_b)
        norms = np.linalg.norm(face_normals, axis=1, keepdims=True)
        norms[norms < 1e-12] = 1.0
        face_normals = face_normals / norms

        mean_edge = float(np.mean(edge_lengths)) if edge_lengths else 0.05
        median_edge = float(np.median(edge_lengths)) if edge_lengths else 0.05

        self._face_graph_cache[mesh_name] = (
            fingerprint, face_adj, edge_to_faces,
            mean_edge, median_edge, vf_1ring,
            face_centers, face_normals)

        return (face_adj, edge_to_faces, mean_edge, median_edge,
                vf_1ring, face_centers, face_normals)


    def _project_curve_to_faces(self, rs, dense_pts):
        try:
            sel = om.MSelectionList()
            sel.add(rs.triangulate_name)
            fn = om.MFnMesh(sel.getDagPath(0))
        except Exception as e:
            log.warn("[权重] 面投影初始化失败: {}".format(e))
            return []

        n_faces = rs.core.face_count
        result = []
        for p in dense_pts:
            try:
                _, fid = fn.getClosestPoint(
                    om.MPoint(float(p[0]), float(p[1]), float(p[2])),
                    om.MSpace.kWorld)
                if 0 <= fid < n_faces:
                    result.append(int(fid))
            except Exception:
                pass
        return result


    def _project_curve_to_faces_with_coords(self, rs, dense_pts):
        try:
            sel = om.MSelectionList()
            sel.add(rs.triangulate_name)
            fn = om.MFnMesh(sel.getDagPath(0))
        except Exception as e:
            log.warn("[权重] 面投影初始化失败: {}".format(e))
            return None, None

        n_faces = rs.core.face_count
        fids = []
        coords = np.zeros((len(dense_pts), 3), dtype=np.float64)
        for i, p in enumerate(dense_pts):
            try:
                cp, fid = fn.getClosestPoint(
                    om.MPoint(float(p[0]), float(p[1]), float(p[2])),
                    om.MSpace.kWorld)
                if 0 <= fid < n_faces:
                    fids.append(int(fid))
                    coords[i] = (cp.x, cp.y, cp.z)
                else:
                    fids.append(-1)
                    coords[i] = p
            except Exception:
                fids.append(-1)
                coords[i] = p
        return fids, coords


    def _project_curve_to_surface(self, rs, points):
        pts = np.asarray(points, dtype=float)
        if len(pts) == 0:
            return pts
        try:
            return rs.project_to_surface(pts)
        except Exception:
            return pts
