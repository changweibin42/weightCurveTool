# -*- coding: utf-8 -*-
"""Surface boundary regions on a virtual cut mesh (no Maya dependency).

Original vertex IDs never change. Curves are traced through neighbouring
triangles, which are subdivided *in memory*. Flooding runs on the resulting
surface cells, not on a 3D nearest-curve sign test. Only numpy is required.
"""
from collections import defaultdict, deque
import heapq
import math

import numpy as np


class RegionError(ValueError):
    pass


def _cross(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def _bary(p, tri):
    a, b = tri[1] - tri[0], tri[2] - tri[0]
    aa, ab, bb = np.dot(a, a), np.dot(a, b), np.dot(b, b)
    den = aa * bb - ab * ab
    if den <= max(aa * bb, 1e-300) * 1e-14:
        raise RegionError("Degenerate triangle on boundary path")
    d = p - tri[0]
    v = (bb * np.dot(d, a) - ab * np.dot(d, b)) / den
    w = (aa * np.dot(d, b) - ab * np.dot(d, a)) / den
    return np.array([1.0 - v - w, v, w])


class _CutMesh(object):
    def __init__(self, vertices, faces):
        self.vertices = np.asarray(vertices, dtype=float)
        self.faces = np.asarray(faces, dtype=np.int64)
        if (self.vertices.ndim != 2 or self.vertices.shape[1] != 3
                or self.faces.ndim != 2 or self.faces.shape[1] != 3
                or not np.all(np.isfinite(self.vertices))):
            raise RegionError("Expected finite vertices and triangular faces")
        if self.faces.size == 0 or self.faces.min() < 0 or self.faces.max() >= len(self.vertices):
            raise RegionError("Invalid surface topology")
        self.positions = [p.copy() for p in self.vertices]
        self.edge_faces = defaultdict(list)
        for fid, face in enumerate(self.faces):
            for a, b in zip(face, np.roll(face, -1)):
                self.edge_faces[tuple(sorted((int(a), int(b))))].append(fid)
        self.adj = [[] for _ in self.faces]
        for edge, fs in self.edge_faces.items():
            if len(fs) > 2:
                raise RegionError("Non-manifold mesh edge: {}".format(edge))
            if len(fs) == 2:
                self.adj[fs[0]].append((fs[1], edge))
                self.adj[fs[1]].append((fs[0], edge))
        self.centers = self.vertices[self.faces].mean(axis=1)
        self.edge_nodes = defaultdict(list)
        self.face_nodes = defaultdict(set)
        self.face_cuts = defaultdict(list)
        self.interior_nodes = {}
        self.labels = {}
        self.curve_nodes = defaultdict(set)
        self.scale = float(np.median([
            np.linalg.norm(self.vertices[a] - self.vertices[b])
            for a, b in self.edge_faces]))
        self.eps = max(self.scale * 1e-9, 1e-12)

    def _edge_node(self, edge, t):
        a, b = edge
        if t <= 1e-8:
            return a
        if t >= 1.0 - 1e-8:
            return b
        for old_t, node in self.edge_nodes[edge]:
            if abs(old_t - t) <= 1e-8:
                return node
        node = len(self.positions)
        self.positions.append(self.vertices[a] * (1.0 - t) + self.vertices[b] * t)
        self.edge_nodes[edge].append((float(t), node))
        return node

    def _node(self, fid, p):
        face = self.faces[fid]
        weights = _bary(np.asarray(p), self.vertices[face])
        if weights.min() < -1e-5:
            raise RegionError("Projected point is outside its recorded triangle")
        weights = np.clip(weights, 0.0, 1.0)
        weights /= weights.sum()
        if weights.max() >= 1.0 - 1e-8:
            node = int(face[np.argmax(weights)])
        elif weights.min() <= 1e-8:
            ids = [int(face[i]) for i in range(3) if i != int(np.argmin(weights))]
            edge = tuple(sorted(ids))
            j = list(face).index(edge[1])
            t = weights[j] / sum(weights[list(face).index(v)] for v in edge)
            node = self._edge_node(edge, float(t))
        else:
            key = (int(fid),) + tuple(np.round(weights[:2], 10))
            node = self.interior_nodes.get(key)
            if node is None:
                node = len(self.positions)
                self.positions.append(weights @ self.vertices[face])
                self.interior_nodes[key] = node
        self.face_nodes[fid].add(node)
        return node

    def _path(self, start, end, p, q):
        if start == end:
            return [start], []
        delta = q - p
        length2 = max(float(np.dot(delta, delta)), self.eps ** 2)
        queue = [(0.0, int(start))]
        dist = {int(start): 0.0}
        parent = {}
        # Curves must be densely sampled. Reject discontinuous projection
        # instead of jumping to a nearby sheet or taking a path round a limb.
        limit = max(np.linalg.norm(delta) * 8.0, self.scale * 4.0)
        while queue:
            cost, cur = heapq.heappop(queue)
            if cost != dist[cur]:
                continue
            if cur == end:
                path, edges = [cur], []
                while cur != start:
                    prev, edge = parent[cur]
                    path.append(prev)
                    edges.append(edge)
                    cur = prev
                return path[::-1], edges[::-1]
            if len(dist) > 4096:
                break
            for nb, edge in self.adj[cur]:
                center = self.centers[nb]
                t = np.clip(np.dot(center - p, delta) / length2, 0.0, 1.0)
                off = np.linalg.norm(center - (p + t * delta))
                step = np.linalg.norm(self.centers[cur] - center)
                new = cost + step * (1.0 + off / max(self.scale, self.eps))
                if new <= limit and new < dist.get(nb, float('inf')):
                    dist[nb] = new
                    parent[nb] = (cur, edge)
                    heapq.heappush(queue, (new, nb))
        raise RegionError("Boundary projection is discontinuous between faces {} and {}".format(start, end))

    def _crossings(self, path, edges, p, q):
        if not edges:
            return []
        first = self.faces[path[0]]
        tri = self.vertices[first]
        u = tri[1] - tri[0]
        u /= np.linalg.norm(u)
        n = np.cross(u, tri[2] - tri[0])
        n /= np.linalg.norm(n)
        v = np.cross(n, u)
        xy = {int(vid): np.array([np.dot(pos - tri[0], u), np.dot(pos - tri[0], v)])
              for vid, pos in zip(first, tri)}
        strips = [xy]
        for k, edge in enumerate(edges):
            a, b = edge
            old = strips[-1]
            xa, xb = old[a], old[b]
            direction = xb - xa
            length = np.linalg.norm(direction)
            unit = direction / length
            perp = np.array([-unit[1], unit[0]])
            prev_third = next(int(i) for i in self.faces[path[k]] if i not in edge)
            new_third = next(int(i) for i in self.faces[path[k + 1]] if i not in edge)
            ra = np.linalg.norm(self.vertices[new_third] - self.vertices[a])
            rb = np.linalg.norm(self.vertices[new_third] - self.vertices[b])
            x = (ra * ra - rb * rb + length * length) / (2.0 * length)
            height = math.sqrt(max(0.0, ra * ra - x * x))
            sign = -1.0 if _cross(direction, old[prev_third] - xa) >= 0 else 1.0
            strips.append({a: xa, b: xb, new_third: xa + x * unit + sign * height * perp})
        p2 = _bary(p, tri) @ np.array([strips[0][int(i)] for i in first])
        last = self.faces[path[-1]]
        q2 = _bary(q, self.vertices[last]) @ np.array([strips[-1][int(i)] for i in last])
        d = q2 - p2
        result, last_t = [], -1e-7
        for k, edge in enumerate(edges):
            a, b = edge
            xa, xb = strips[k][a], strips[k][b]
            e = xb - xa
            den = _cross(d, e)
            if abs(den) <= max(np.linalg.norm(d) * np.linalg.norm(e), 1e-300) * 1e-12:
                # A path exactly along a mesh edge is represented by its
                # endpoint nodes; choose the endpoint on this unfolded edge.
                candidates = []
                for t, point in ((0.0, p2), (1.0, q2)):
                    s = np.dot(point - xa, e) / np.dot(e, e)
                    if -1e-7 <= s <= 1.0 + 1e-7 and np.linalg.norm(point - (xa + s * e)) <= self.eps * 10:
                        candidates.append((t, s))
                if not candidates:
                    raise RegionError("Cannot unfold boundary segment; add a curve anchor near this corner")
                t, s = min(candidates, key=lambda item: abs(item[0] - max(last_t, 0.0)))
            else:
                t = _cross(xa - p2, e) / den
                s = _cross(xa - p2, d) / den
            if t < last_t - 1e-6 or not (-1e-6 <= t <= 1.0 + 1e-6 and -1e-6 <= s <= 1.0 + 1e-6):
                raise RegionError("Boundary face path does not follow the surface segment; add a curve anchor near this corner")
            last_t = max(last_t, t)
            result.append((edge, float(np.clip(s, 0.0, 1.0))))
        return [self._edge_node(edge, s) for edge, s in result]

    def _segment_path(self, start, end, p, q):
        path, edges = self._path(start, end, p, q)
        try:
            return path, self._crossings(path, edges, p, q)
        except RegionError:
            pass
        # A shortest *face-centre* path can go round the wrong side of a
        # vertex. Enumerate local alternative strips and accept only one for
        # which the unfolded segment actually crosses every recorded edge.
        queue = [(0.0, 0.0, (int(start),), ())]
        limit = max(np.linalg.norm(q - p) * 8.0, self.scale * 4.0)
        attempts = 0
        while queue and attempts < 1024:
            _, cost, path, edges = heapq.heappop(queue)
            attempts += 1
            cur = path[-1]
            if cur == end:
                try:
                    return list(path), self._crossings(path, edges, p, q)
                except RegionError:
                    continue
            if len(path) > 32:
                continue
            for nb, edge in self.adj[cur]:
                if nb in path:
                    continue
                step = float(np.linalg.norm(self.centers[nb] - self.centers[cur]))
                new = cost + step
                if new <= limit:
                    estimate = new + float(np.linalg.norm(self.centers[nb] - self.centers[end]))
                    heapq.heappush(queue, (estimate, new, path + (nb,), edges + (edge,)))
        raise RegionError("Cannot trace boundary through a surface corner; add an anchor near this corner")

    def trace(self, points, fids, label, closed):
        points = np.asarray(points, dtype=float)
        fids = np.asarray(fids, dtype=np.int64)
        if len(points) < 2 or len(points) != len(fids) or not np.all(np.isfinite(points)):
            raise RegionError("Incomplete projected curve")
        if np.any(fids < 0) or np.any(fids >= len(self.faces)):
            raise RegionError("Curve projection contains invalid face IDs")
        count = len(points) if closed else len(points) - 1
        for i in range(count):
            j = (i + 1) % len(points)
            p, q = points[i], points[j]
            if np.linalg.norm(p - q) <= self.eps:
                continue
            path, crossings = self._segment_path(int(fids[i]), int(fids[j]), p, q)
            nodes = [self._node(int(fids[i]), p)]
            nodes.extend(crossings)
            nodes.append(self._node(int(fids[j]), q))
            for fid, a, b in zip(path, nodes, nodes[1:]):
                if a == b:
                    continue
                self.face_nodes[fid].update((a, b))
                self.face_cuts[fid].append((a, b, label))
                self.curve_nodes[label].update((a, b))

    def _face_cells(self, fid):
        face = list(map(int, self.faces[fid]))
        nodes = set(face) | self.face_nodes[fid]
        frame = self.vertices[face]
        u = frame[1] - frame[0]
        u /= np.linalg.norm(u)
        v = frame[2] - frame[0]
        v -= np.dot(v, u) * u
        v /= np.linalg.norm(v)
        def point(n):
            d = self.positions[n] - frame[0]
            return np.array([np.dot(d, u), np.dot(d, v)])
        raw_edges = []
        for a, b in zip(face, face[1:] + face[:1]):
            edge = tuple(sorted((a, b)))
            nodes.update(n for _, n in self.edge_nodes[edge])
            raw_edges.append((a, b, None))
        raw_edges.extend(self.face_cuts[fid])
        xy = {n: point(n) for n in nodes}
        cuts = self.face_cuts[fid]
        # Curves cannot cross or overlap. No global 3D distance is used here.
        for i, (a, b, label) in enumerate(cuts):
            for c, d, other in cuts[:i]:
                if len({a, b, c, d}) < 4:
                    if label != other and label != 3 and other != 3:
                        raise RegionError("Ridge/FWD/BWD curves touch or intersect")
                    continue
                ab, cd = xy[b] - xy[a], xy[d] - xy[c]
                den = _cross(ab, cd)
                if abs(den) > self.eps ** 2:
                    t = _cross(xy[c] - xy[a], cd) / den
                    s = _cross(xy[c] - xy[a], ab) / den
                    if 1e-8 < t < 1 - 1e-8 and 1e-8 < s < 1 - 1e-8:
                        raise RegionError("Boundary curve crosses itself or another curve")
        graph = defaultdict(set)
        for a, b, label in raw_edges:
            delta = xy[b] - xy[a]
            length2 = np.dot(delta, delta)
            if length2 <= self.eps ** 2:
                continue
            along = []
            for node in nodes:
                t = np.dot(xy[node] - xy[a], delta) / length2
                if -1e-8 <= t <= 1 + 1e-8 and abs(_cross(delta, xy[node] - xy[a])) <= self.eps * math.sqrt(length2):
                    along.append((float(t), node))
            along.sort()
            for (_, na), (_, nb) in zip(along, along[1:]):
                if na == nb:
                    continue
                graph[na].add(nb)
                graph[nb].add(na)
                if label is not None:
                    edge = tuple(sorted((na, nb)))
                    old = self.labels.get(edge)
                    if old is not None and old != label:
                        raise RegionError("Different boundary curves overlap")
                    self.labels[edge] = label
                    self.curve_nodes[label].update((na, nb))
        # A closed curve wholly inside one triangle needs explicit surface
        # refinement; do not guess a region from overlapping planar cycles.
        reached, queue = set(), [face[0]]
        while queue:
            node = queue.pop()
            if node not in reached:
                reached.add(node)
                queue.extend(graph[node] - reached)
        if len(reached) != len(graph):
            raise RegionError("A closed curve fits inside one triangle; subdivide this mesh area first")
        ordered = {a: sorted(bs, key=lambda b: math.atan2(*(xy[b] - xy[a])[::-1]))
                   for a, bs in graph.items()}
        visited, cells = set(), []
        for a in graph:
            for b in ordered[a]:
                if (a, b) in visited:
                    continue
                cycle, current = [], (a, b)
                while current not in visited:
                    visited.add(current)
                    x, y = current
                    cycle.append(x)
                    neighbours = ordered[y]
                    z = neighbours[(neighbours.index(x) - 1) % len(neighbours)]
                    current = (y, z)
                area = sum(_cross(xy[x], xy[y]) for x, y in zip(cycle, cycle[1:] + cycle[:1]))
                if area > self.eps ** 2:
                    if len(set(cycle)) != len(cycle):
                        raise RegionError("Boundary has a dangling or self-touching segment")
                    cells.append(cycle)
        if not cells:
            raise RegionError("No valid surface cell in triangle {}".format(fid))
        return cells

    def cells(self):
        cells = []
        incidence = defaultdict(list)
        for fid in range(len(self.faces)):
            for polygon in self._face_cells(fid):
                cid = len(cells)
                cells.append(polygon)
                for a, b in zip(polygon, polygon[1:] + polygon[:1]):
                    incidence[tuple(sorted((a, b)))].append(cid)
        # Check assembled curves are continuous closed loops (or meet a cap).
        degrees = defaultdict(lambda: defaultdict(int))
        node_labels = defaultdict(set)
        for (a, b), label in self.labels.items():
            degrees[label][a] += 1
            degrees[label][b] += 1
            if label != 3:
                node_labels[a].add(label)
                node_labels[b].add(label)
        if any(len(labels) > 1 for labels in node_labels.values()):
            raise RegionError("Ridge/FWD/BWD curves touch or intersect")
        for label in (0, 1, 2):
            if not degrees[label]:
                raise RegionError("Empty boundary curve")
            for node, degree in degrees[label].items():
                if degree != 2 and not (degree == 1 and node in self.curve_nodes[3]):
                    raise RegionError("Boundary curve is open or self-touching")
        adj = [[] for _ in cells]
        for edge, ids in incidence.items():
            if len(ids) > 2:
                raise RegionError("Non-manifold virtual surface")
            if len(ids) == 2 and edge not in self.labels:
                a, b = ids
                adj[a].append(b)
                adj[b].append(a)
        component = np.full(len(cells), -1, dtype=np.int64)
        groups = []
        for seed in range(len(cells)):
            if component[seed] >= 0:
                continue
            group, queue = [], deque([seed])
            component[seed] = len(groups)
            while queue:
                cur = queue.popleft()
                group.append(cur)
                for nb in adj[cur]:
                    if component[nb] < 0:
                        component[nb] = len(groups)
                        queue.append(nb)
            groups.append(group)
        touching = defaultdict(set)
        for edge, label in self.labels.items():
            ids = incidence.get(edge, [])
            if len(ids) != 2 and label != 3:
                raise RegionError("Boundary lies on an open mesh edge; cannot define both sides")
            touching[label].update(int(component[c]) for c in ids)
        return cells, groups, touching

    def distances(self, cells, group, label):
        graph = defaultdict(dict)
        for cid in group:
            polygon = cells[cid]
            # Cell edges and ear diagonals preserve narrow corridors without
            # introducing shortcuts across a concave boundary.
            for a, b in zip(polygon, polygon[1:] + polygon[:1]):
                length = float(np.linalg.norm(self.positions[a] - self.positions[b]))
                graph[a][b] = length
                graph[b][a] = length
            for a, b in _triangulate_polygon(polygon, self.positions, self.eps):
                length = float(np.linalg.norm(self.positions[a] - self.positions[b]))
                graph[a][b] = length
                graph[b][a] = length
        dist = np.full(len(self.positions), np.inf)
        queue = []
        for node in self.curve_nodes[label]:
            if node in graph:
                dist[node] = 0.0
                heapq.heappush(queue, (0.0, node))
        while queue:
            d, node = heapq.heappop(queue)
            if d != dist[node]:
                continue
            for nb, length in graph[node].items():
                new = d + length
                if new < dist[nb]:
                    dist[nb] = new
                    heapq.heappush(queue, (new, nb))
        return dist


def _triangulate_polygon(polygon, positions, eps):
    """Ear clipping of a simple planar cell; return internal graph edges."""
    if len(polygon) <= 3:
        return []
    points = np.array([positions[i] for i in polygon])
    origin = points[0]
    _, _, axes = np.linalg.svd(points - points.mean(axis=0), full_matrices=False)
    xy = (points - origin) @ axes[:2].T
    area = sum(_cross(a, b) for a, b in zip(xy, np.roll(xy, -1, axis=0)))
    sign = 1.0 if area >= 0 else -1.0
    work, diagonals = list(range(len(polygon))), []
    while len(work) > 3:
        removed = False
        for j, b in enumerate(work):
            a, c = work[j - 1], work[(j + 1) % len(work)]
            turn = sign * _cross(xy[b] - xy[a], xy[c] - xy[b])
            if abs(turn) <= eps ** 2:
                diagonals.append((polygon[a], polygon[c]))
                work.pop(j)
                removed = True
                break
            if turn < 0:
                continue
            inside = False
            for k in work:
                if k in (a, b, c):
                    continue
                sides = [sign * _cross(xy[y] - xy[x], xy[k] - xy[x])
                         for x, y in ((a, b), (b, c), (c, a))]
                if min(sides) >= -eps ** 2:
                    inside = True
                    break
            if not inside:
                diagonals.append((polygon[a], polygon[c]))
                work.pop(j)
                removed = True
                break
        if not removed:
            raise RegionError("Cannot triangulate a boundary cell")
    return diagonals


def solve_surface_band(vertices, faces, curves, closed=True):
    """curves = [(ridge_xyz, ridge_face_ids), (fwd_xyz, fids), (bwd_xyz, fids)].

    Return exact original-vertex membership in the traced polyline region,
    plus graph-geodesic ridge-to-boundary coordinates for each side. These
    distances are a discretization; they are not exact continuous geodesics.
    """
    cut = _CutMesh(vertices, faces)
    for label, (points, fids) in enumerate(curves):
        cut.trace(points, fids, label, closed)
    if not closed:
        # End caps follow the surface via the corresponding ridge endpoint.
        for end in (0, -1):
            points = np.array([curves[i][0][end] for i in (1, 0, 2)])
            fids = np.array([curves[i][1][end] for i in (1, 0, 2)])
            cut.trace(points, fids, 3, False)
    cells, groups, touching = cut.cells()
    ridge_sides = touching[0]
    fwd = (ridge_sides & touching[1]) - touching[2]
    bwd = (ridge_sides & touching[2]) - touching[1]
    if len(ridge_sides) != 2 or len(fwd) != 1 or len(bwd) != 1:
        raise RegionError("Three curves do not enclose two distinct ridge-to-boundary strips")
    fwd_cid, bwd_cid = next(iter(fwd)), next(iter(bwd))
    n = len(vertices)
    mask, side, t = np.zeros(n, bool), np.zeros(n, np.int8), np.zeros(n)
    for cid, label, direction in ((fwd_cid, 1, 1), (bwd_cid, 2, -1)):
        ids = np.array(sorted({v for c in groups[cid] for v in cells[c] if v < n}), dtype=int)
        dr = cut.distances(cells, groups[cid], 0)[ids]
        db = cut.distances(cells, groups[cid], label)[ids]
        if not np.all(np.isfinite(dr + db)):
            raise RegionError("Unreachable vertices in virtual surface strip")
        mask[ids], side[ids] = True, direction
        t[ids] = dr / np.maximum(dr + db, cut.eps)
    boundary_masks = []
    for label in (0, 1, 2):
        bm = np.zeros(n, bool)
        bm[[i for i in cut.curve_nodes[label] if i < n]] = True
        boundary_masks.append(bm)
    ridge, fwd_boundary, bwd_boundary = boundary_masks
    mask |= ridge | fwd_boundary | bwd_boundary
    t[ridge] = 0.0
    t[fwd_boundary | bwd_boundary] = 1.0
    side[fwd_boundary], side[bwd_boundary] = 1, -1
    outer = np.zeros(n, bool)
    for cid in touching[1] - {fwd_cid, bwd_cid}:
        outer[[v for c in groups[cid] for v in cells[c] if v < n]] = True
    outer &= ~mask
    return dict(mask=mask, side=side, t=np.clip(t, 0.0, 1.0), ridge=ridge,
                fwd_boundary=fwd_boundary, bwd_boundary=bwd_boundary,
                fwd_outer=outer,
                shared_exterior=bool((touching[1] & touching[2]) - {fwd_cid, bwd_cid}),
                cells=len(cells), components=len(groups))
