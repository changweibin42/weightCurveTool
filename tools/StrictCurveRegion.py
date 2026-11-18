# -*- coding: utf-8 -*-
"""V17.9.0: optional audited native strict queries; V17.8.17: raw boundary-query reuse; V17.8.16: mesh graph/topology reuse; V17.8.14: exact surface-sample reuse; V17.8.10: guarded adaptive ridge constraints; V17.3.5:  recover enclosed band and FWD-exterior selection holes; face-witness crossings; three-curve Dirichlet field + topology-side surface-distance decay of maya_test_curve_band_strict.py.

Membership is the same topology + local NURBS refinement validated by the
user. Membership is independent of Soft/IK and Bezier values. This module
never edits scene geometry, selection, skin weights or curve attributes.
"""
import math
import heapq
from collections import Counter, deque
import numpy as np
import maya.cmds as cmds

from ..Logger import log
import maya.api.OpenMaya as om
try:
    from .NativeEdgeCuts import compute_if_enabled as _native_cut_backend
except ImportError:
    _native_cut_backend = None

try:
    from .CurveSurfaceSamples import mesh_signature as _sample_mesh_signature
    from .CurveSurfaceSamples import sample_if_unchanged as _cached_curve_samples
except ImportError:
    _sample_mesh_signature = _cached_curve_samples = None

try:
    from .MeshRegionCache import prepare_region_mesh as _prepare_region_mesh
except ImportError:
    _prepare_region_mesh = None

try:
    from .BoundaryQueryCache import prepare_queries as _prepare_boundary_queries
except ImportError:
    _prepare_boundary_queries = None

STRICT_EPS_FACTOR = 1e-7
REFINE_RINGS = 3
SAMPLE_SPACING = .20

def _wct_shape(name, kind):
    matches = cmds.ls(name, long=True) or []
    if len(matches) != 1:
        raise RuntimeError('Missing or ambiguous node: ' + name)
    node = matches[0]
    if cmds.nodeType(node) != kind:
        shapes = cmds.listRelatives(node, shapes=True, noIntermediate=True,
                                    fullPath=True, type=kind) or []
        if len(shapes) != 1:
            raise RuntimeError('Expected one {} shape: {}'.format(kind, name))
        node = shapes[0]
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def _wct_dist(a, b):
    return (a - b).length()


def _wct_path(a, b, graph, points, median):
    if a == b:
        return [a]
    delta = points[b] - points[a]
    den = max(delta * delta, 1e-30)
    budget = max(12.0 * math.sqrt(den), 6.0 * median)
    queue, costs, previous = [(0.0, a)], {a: 0.0}, {}
    while queue:
        cost, cur = heapq.heappop(queue)
        if cost != costs[cur]:
            continue
        if cur == b:
            path = [b]
            while path[-1] != a:
                path.append(previous[path[-1]])
            return path[::-1]
        for nb, length in graph[cur].items():
            t = max(0.0, min(1.0, ((points[nb] - points[a]) * delta) / den))
            off = _wct_dist(points[nb], points[a] + delta * t)
            new = cost + length * (1.0 + 3.0 * off / median)
            if new <= budget and new < costs.get(nb, float('inf')):
                costs[nb], previous[nb] = new, cur
                heapq.heappush(queue, (new, nb))
    raise RuntimeError('Cannot connect boundary vertices {} -> {}; projection may jump sheets'.format(a, b))


def _wct_loop(anchors, graph, points, median, label):
    edges = set()
    for a, b in zip(anchors, anchors[1:] + anchors[:1]):
        path = _wct_path(a, b, graph, points, median)
        edges.update(tuple(sorted((x, y))) for x, y in zip(path, path[1:]))
    links = {}
    for a, b in edges:
        links.setdefault(a, set()).add(b)
        links.setdefault(b, set()).add(a)
    # Remove sampling backtracking spurs; they are not part of a closed loop.
    leaves = deque(n for n in links if len(links[n]) < 2)
    removed = 0
    while leaves:
        a = leaves.popleft()
        if a not in links:
            continue
        for b in list(links[a]):
            links[b].discard(a)
            if len(links[b]) < 2:
                leaves.append(b)
        del links[a]
        removed += 1
    if not links or any(len(bs) != 2 for bs in links.values()):
        raise RuntimeError(label + ': snapped boundary is not a simple loop; refine the local mesh')
    seen, queue = set(), [next(iter(links))]
    while queue:
        a = queue.pop()
        if a not in seen:
            seen.add(a)
            queue.extend(links[a] - seen)
    if len(seen) != len(links):
        raise RuntimeError(label + ': snapping produced multiple loops')
    log.info('[RangeTest] {}: {} loop vertices, {} spur vertices removed'.format(label, len(seen), removed))
    return seen


def _wct_expand(vertices, graph, rings):
    cached=getattr(graph,'expand_vertices',None)
    if cached is not None:return cached(vertices,rings)
    result, front = set(vertices), set(vertices)
    for _ in range(rings):
        front = {nb for v in front for nb in graph[v]} - result
        result.update(front)
    return result


def _wct_strict_refine(mesh, points, graph, interior, fwd, bwd, median, curve_names, query_session=None):
    """Only local boundary vertices are judged by a boundary tangent plane.

    Topology still controls the large region. Never apply a global nearest-line
    halfspace to an entire folded mesh. The inside direction is calibrated
    from neighbouring vertices of the already identified interior component.
    """
    tolerance = max(median * STRICT_EPS_FACTOR, 1e-10)
    nearby = [_wct_expand(loop, graph, REFINE_RINGS) for loop in (fwd, bwd)]
    candidates = set(interior) | nearby[0] | nearby[1]
    tests, signs = [], []
    for label, name, loop, near in zip(('FWD', 'BWD'), (curve_names[1], curve_names[2]), (fwd, bwd), nearby):
        path=_wct_shape(name,'nurbsCurve')
        fn=om.MFnNurbsCurve(path)
        cache={};boundary_mesh=mesh
        if query_session is not None:
            fn,boundary_mesh,cache=query_session.boundary(label,path,fn,vertices=near)
        def raw(v, curve=fn, local_cache=cache, surface=boundary_mesh):
            if v in local_cache:
                return local_cache[v]
            p = points[v]
            cp, param = curve.closestPoint(p, tolerance=tolerance * .1,
                                           space=om.MSpace.kWorld)
            surface_cp, face = surface.getClosestPoint(cp, om.MSpace.kWorld)
            # Reject a nearest curve point on a different nearby sheet/fold.
            local = _wct_expand((v,), graph, REFINE_RINGS + 1)
            if not (set(surface.getPolygonVertices(face)) & local):
                local_cache[v] = None
                return None
            normal, _ = surface.getClosestNormal(surface_cp, om.MSpace.kWorld)
            tangent = curve.tangent(param, om.MSpace.kWorld)
            side_axis = normal ^ tangent
            length = side_axis.length()
            if length < 1e-12:
                local_cache[v] = None
                return None
            value = ((p - surface_cp) * side_axis) / length
            local_cache[v] = value
            return value
        references = sorted(interior & _wct_expand(loop, graph, 1))
        values = [raw(v) for v in references]
        usable = [x for x in values if x is not None and abs(x) > tolerance]
        if len(usable) < 3:
            raise RuntimeError(label + ': insufficient interior references for strict boundary refinement')
        positive = sum(x > 0 for x in usable)
        agreement = max(positive, len(usable) - positive) / float(len(usable))
        if agreement < .90:
            raise RuntimeError(label + ': inconsistent boundary side; inspect normals or local folding')
        sign = 1.0 if positive > len(usable) / 2.0 else -1.0
        tests.append(raw)
        signs.append(sign)
        log.info('[RangeTest Strict] {}: orientation agreement={:.1%}, references={}'.format(
            label, agreement, len(usable)))
    accepted, outside, on_curve, uncertain = set(), set(), set(), set()
    for v in sorted(candidates):
        values = [signs[i] * tests[i](v) if tests[i](v) is not None else None
                  for i in range(2) if v in nearby[i]]
        if any(x is not None and x < -tolerance for x in values):
            outside.add(v)
        elif any(x is None for x in values):
            uncertain.add(v)
        elif any(abs(x) <= tolerance for x in values):
            on_curve.add(v)
        else:
            accepted.add(v)
    # Do not retain isolated outside islands admitted by a local tangent test.
    seeds = accepted & interior
    if not seeds:
        raise RuntimeError('Strict refinement left no interior seed vertices')
    reached, queue = set(seeds), deque(seeds)
    while queue:
        v = queue.popleft()
        for nb in graph[v]:
            if nb in accepted and nb not in reached:
                reached.add(nb)
                queue.append(nb)
    log.info('[RangeTest Strict] candidates={}, outside={}, on_curve={}, uncertain={}, disconnected={}'.format(
        len(candidates), len(outside), len(on_curve), len(uncertain), len(accepted - reached)))
    return reached, outside, on_curve, uncertain, tests, signs, nearby, tolerance


def _recover_enclosed_interior(graph, strict, coarse_interior, boundary_face_vertices):
    """Recover only enclosed holes in the known topology strip.

    A component must be completely within its known coarse topology domain,
    surrounded by accepted vertices, and absent from every face
    visited by FWD/BWD. This is not a weight threshold or a global dilation.
    A hole containing genuine boundary-curve evidence is never filled.
    """
    strict=set(strict)
    coarse=set(coarse_interior)
    boundary=set(boundary_face_vertices)
    remaining=set(range(len(graph)))-strict
    recovered=set()
    while remaining:
        seed=next(iter(remaining));remaining.remove(seed)
        component={seed};queue=[seed];touches_strict=False
        while queue:
            v=queue.pop()
            for nb in graph[v]:
                if nb in strict:
                    touches_strict=True
                elif nb in remaining:
                    remaining.remove(nb);component.add(nb);queue.append(nb)
        if touches_strict and component<=coarse and not (component & boundary):
            recovered.update(component)
    return strict | recovered, sorted(recovered)


def build_strict_region(mesh_name, curve_names):
    """Use the user-validated current-pose diagnostic, without selection/writes.

    curve_names is ordered (ridge, fwd, bwd). No rest-space remapping is used
    for membership: it must agree with the tested visible mesh/curves.
    """
    mesh_path = _wct_shape(mesh_name, 'mesh')
    mesh = om.MFnMesh(mesh_path)
    points = mesh.getPoints(om.MSpace.kWorld)
    count = mesh.numVertices
    def compute_graph():
        graph, lengths = [dict() for _ in range(count)], []
        it = om.MItMeshEdge(mesh_path)
        while not it.isDone():
            a, b = it.vertexId(0), it.vertexId(1)
            length = _wct_dist(points[a], points[b])
            if length <= 1e-12:
                raise RuntimeError('Zero-length mesh edge: ' + str(it.index()))
            graph[a][b] = graph[b][a] = length
            lengths.append(length)
            it.next()
        if not lengths:
            raise RuntimeError('Mesh has no edges')
        median = sorted(lengths)[len(lengths) // 2]
        return graph,median
    prepare=globals().get('_prepare_region_mesh')
    if prepare:
        mesh,graph,median=prepare(mesh_path,mesh,points,compute_graph)
    else:
        graph,median=compute_graph()

    curve_samples = {}
    sample_backend=globals().get('_cached_curve_samples')
    signature=globals().get('_sample_mesh_signature')
    sample_mesh_key=None
    if sample_backend and signature:
        try:
            sample_mesh_key=signature(mesh_path,mesh,points,median,SAMPLE_SPACING)
        except Exception as problem:
            log.info('[WCT SampleCache V17.8.14] mesh uncached: '+str(problem))
    def sample(curve_name):
        curve_path=_wct_shape(curve_name, 'nurbsCurve')
        fn = om.MFnNurbsCurve(curve_path)
        if fn.form == om.MFnNurbsCurve.kOpen:
            raise RuntimeError('This test requires CLOSED curves: ' + curve_name)
        def compute():
            length = fn.length()
            samples = max(128, int(math.ceil(length / (median * SAMPLE_SPACING))))
            if samples > 20000:
                raise RuntimeError('Too many samples; check curve scale: ' + curve_name)
            anchors = []
            surface_samples = []
            max_offset = 0.0
            for i in range(samples):
                param = fn.findParamFromLength(length * i / samples)
                p = fn.getPointAtParam(param, om.MSpace.kWorld)
                projected, face = mesh.getClosestPoint(p, om.MSpace.kWorld)
                surface_samples.append((projected, int(face), float(param)))
                max_offset = max(max_offset, _wct_dist(p, projected))
                ids = mesh.getPolygonVertices(face)
                nearest = min(ids, key=lambda v: _wct_dist(points[v], projected))
                if not anchors or anchors[-1] != nearest:
                    anchors.append(nearest)
            if len(anchors) > 1 and anchors[-1] == anchors[0]:
                anchors.pop()
            return anchors,surface_samples,max_offset
        if sample_mesh_key is not None:
            anchors,surface_samples,max_offset=sample_backend(sample_mesh_key,curve_path,fn,compute)
        else:
            anchors,surface_samples,max_offset=compute()
        log.info('[RangeTest] {}: samples={}, max surface offset={:.6f}'.format(
            curve_name, len(surface_samples), max_offset))
        curve_samples[curve_name] = surface_samples
        return anchors

    fwd_anchors, bwd_anchors = sample(curve_names[1]), sample(curve_names[2])
    fwd = _wct_loop(fwd_anchors, graph, points, median, 'FWD')
    bwd = _wct_loop(bwd_anchors, graph, points, median, 'BWD')
    if fwd & bwd:
        raise RuntimeError('Snapped FWD/BWD loops overlap; strip is too narrow for this test resolution')
    blocked = fwd | bwd
    labels, components = [-1] * count, []
    for seed in range(count):
        if seed in blocked or labels[seed] >= 0:
            continue
        cid = len(components)
        labels[seed] = cid
        queue, group = deque([seed]), []
        while queue:
            cur = queue.popleft()
            group.append(cur)
            for nb in graph[cur]:
                if nb not in blocked and labels[nb] < 0:
                    labels[nb] = cid
                    queue.append(nb)
        components.append(group)
    ridge = sample(curve_names[0])
    votes = Counter(labels[v] for v in ridge if labels[v] >= 0)
    if not votes:
        raise RuntimeError('Ridge falls on snapped boundary; no interior seeds at this mesh resolution')
    target, hits = votes.most_common(1)[0]
    ratio = hits / float(sum(votes.values()))
    log.info('[RangeTest] component sizes={}, ridge votes={}, agreement={:.1%}'.format(
        [len(c) for c in components], dict(votes), ratio))
    if ratio < .90:
        raise RuntimeError('Ridge spans several regions; inspect curve projection and loop snapping')
    interior = set(components[target])
    for label, loop in (('FWD', fwd), ('BWD', bwd)):
        if not any(nb in interior for v in loop for nb in graph[v]):
            raise RuntimeError('Ridge region does not touch ' + label)
    if len(components) < 2:
        raise RuntimeError('Boundary loops did not separate a region; selection cancelled')
    query_session=None
    prepare_queries=globals().get('_prepare_boundary_queries')
    if prepare_queries:
        query_session=prepare_queries(mesh,points,graph,
            max(median*STRICT_EPS_FACTOR,1e-10),REFINE_RINGS+1,mesh_name=mesh_name)
    strict, outside, on_curve, uncertain, tests, signs, nearby, tolerance = _wct_strict_refine(
        mesh, points, graph, interior, fwd, bwd, median, curve_names,query_session=query_session)
    raw_strict_count=len(strict)
    boundary_faces={int(face) for name in curve_names[1:]
                    for _,face,_ in curve_samples[name]}
    boundary_vertices={int(v) for face in boundary_faces for v in mesh.getPolygonVertices(face)}
    strict,recovered_holes=_recover_enclosed_interior(graph,strict,interior,boundary_vertices)
    outside.difference_update(recovered_holes)
    uncertain.difference_update(recovered_holes)
    log.info('[StrictHoles V17.3.5] recovered={}, vertices={}'.format(
        len(recovered_holes),recovered_holes[:32]))
    outer, outer_error = _fwd_exterior(
        graph, components, labels, target, strict, fwd, bwd, tests, signs, nearby, tolerance)
    recovered_outer_holes=[]
    if outer_error is None:
        fwd_cids={labels[nb] for v in fwd for nb in graph[v]
                  if labels[nb]>=0}-{target}
        coarse_outer=set().union(*(set(components[cid]) for cid in fwd_cids))
        # Keep the strict strip and all actual curve-face vertices protected.
        # Only an entire enclosed excluded component in the FWD coarse domain
        # may be restored; no broadening across either real boundary.
        outer,recovered_outer_holes=_recover_enclosed_interior(
            graph,outer,coarse_outer,boundary_vertices|strict)
    log.info('[StrictOuterHoles V17.3.5] recovered={}, vertices={}'.format(
        len(recovered_outer_holes),recovered_outer_holes[:32]))
    log.info('[StrictCurveRegion V17.3.5] strict_band={}, FWD_outer={}, on_curve={}, uncertain={}'.format(
        len(strict), len(outer), len(on_curve), len(uncertain)))
    if query_session is not None:query_session.finish()
    if hasattr(graph,'expansion_hits'):
        log.info('[WCT TopologyCache V17.8.16] ring_hits={} ring_misses={}'.format(
            graph.expansion_hits,graph.expansion_misses))
    return dict(mask=_mask(strict, count), fwd_outer=_mask(outer, count),
                outer_error=outer_error, graph=graph, points=points, median=median,
                ridge_anchors=ridge, fwd_anchors=fwd_anchors,
                bwd_anchors=bwd_anchors, curves=curve_names,
                components=len(components), uncertain=len(uncertain), on_curve=len(on_curve),
                coarse_interior=interior, fwd_loop=fwd, bwd_loop=bwd,
                curve_samples=curve_samples,raw_strict_count=raw_strict_count,
                recovered_interior_holes=recovered_holes,
                recovered_fwd_outer_holes=recovered_outer_holes)


def _mask(ids, count):
    result = np.zeros(count, dtype=bool)
    if ids:
        result[list(ids)] = True
    return result


def _fwd_exterior(graph, components, labels, target, strict, fwd, bwd,
                  tests, signs, nearby, tolerance):
    """Partition exterior by topology, then refine only its boundary fringe.

    Do not re-use the old projected outer mask: every vertex in the connected
    FWD exterior is included, even far away from the nearest ridge section.
    """
    fwd_components = {labels[nb] for v in fwd for nb in graph[v] if labels[nb] >= 0} - {target}
    bwd_components = {labels[nb] for v in bwd for nb in graph[v] if labels[nb] >= 0} - {target}
    shared = fwd_components & bwd_components
    if shared:
        # A closed handle can connect both exteriors. There is no unique
        # topology-only FWD exterior there; do not transfer the BWD side too.
        return set(), 'FWD/BWD 外侧连通，无法唯一分离 FWD 外区；请调整边界或网格开口'
    candidates = set(nearby[0])
    for cid in fwd_components:
        candidates.update(components[cid])
    outer = set()
    for v in candidates - set(strict):
        if v in nearby[0]:
            value = tests[0](v)
            if value is None or signs[0] * value > tolerance:
                continue
        if v in nearby[1]:
            value = tests[1](v)
            if value is None or signs[1] * value <= tolerance:
                continue
        outer.add(v)
    # Include an exterior thinner than one mesh ring, provided it is adjacent
    # to the corresponding FWD loop. Exclude unrelated isolated components.
    seeds = {v for v in outer if any(nb in fwd for nb in graph[v]) or v in fwd}
    for cid in fwd_components:
        seeds.update(outer & set(components[cid]))
    reached, queue = set(seeds), deque(seeds)
    while queue:
        v = queue.popleft()
        for nb in graph[v]:
            if nb in outer and nb not in reached:
                reached.add(nb)
                queue.append(nb)
    return reached, None



def _curve_edge_data(region, mesh, curve, name, eps):
    """Local curve-side evaluators for edge intersections, not vertex regions."""
    graph, points = region['graph'], region['points']
    samples = region['curve_samples'][name]
    # These caches live only for this curve evaluator/one solve. The mesh is
    # unchanged during the solve; no geometry or query result crosses edits.
    polygons = {}
    def polygon_vertices(face):
        if face not in polygons:
            polygons[face] = set(mesh.getPolygonVertices(face))
        return polygons[face]
    sample_vertices = [polygon_vertices(face) for _, face, _ in samples]
    by_vertex = {}
    for i, vertices in enumerate(sample_vertices):
        for vertex in vertices:
            by_vertex.setdefault(vertex, set()).add(i)
    local_candidates = {}
    normals = {}
    counts = region.setdefault('field_query_counts', {})
    def normal_at(point):
        key = (float(point.x), float(point.y), float(point.z))
        if key not in normals:
            normals[key] = mesh.getClosestNormal(point, om.MSpace.kWorld)[0]
            counts['normal_queries'] = counts.get('normal_queries', 0) + 1
        else:
            counts['normal_hits'] = counts.get('normal_hits', 0) + 1
        return normals[key]
    def candidates_for(local):
        key = frozenset(local)
        if key not in local_candidates:
            hits = set()
            for vertex in local:
                hits.update(by_vertex.get(vertex, ()))
            # Preserve the original sample scan order, including distance ties.
            local_candidates[key] = sorted(hits)
        return local_candidates[key]
    touched = set().union(*sample_vertices) if samples else set()
    near = _wct_expand(touched, graph, 1)
    endpoint_cache = dict(region.get('_native_endpoint_cache', {}).get(name, {}))
    def evaluate(point, local):
        counts['evaluate'] = counts.get('evaluate', 0) + 1
        cp, param = curve.closestPoint(point, tolerance=eps*.1, space=om.MSpace.kWorld)
        cp, face = mesh.getClosestPoint(cp, om.MSpace.kWorld)
        if polygon_vertices(face) & local:
            tangent = curve.tangent(param, om.MSpace.kWorld)
            normal = normal_at(cp)
        else:
            # A global 3D closest point may be on another fold. Fall back to
            # locally bound samples, never use that remote point as a cut.
            candidates = candidates_for(local)
            if not candidates:
                return None
            i = min(candidates, key=lambda i:_wct_dist(point, samples[i][0]))
            best = None
            for a, b in (((i-1)%len(samples), i), (i, (i+1)%len(samples))):
                if not (sample_vertices[a] & local and sample_vertices[b] & local):
                    continue
                pa, pb = samples[a][0], samples[b][0]
                delta = pb-pa
                length2 = delta*delta
                if length2 <= eps*eps:
                    continue
                u = max(0., min(1., ((point-pa)*delta)/length2))
                projected = pa + delta*u
                distance = _wct_dist(point, projected)
                if best is None or distance < best[0]:
                    best = (distance, projected, delta)
            if best is None:
                return None
            _, cp, tangent = best
            normal = normal_at(samples[i][0])
        axis = normal ^ tangent
        if axis.length() <= 1e-12:
            return None
        return ((point-cp)*axis)/axis.length(), _wct_dist(point, cp)
    def endpoint(v):
        if v not in endpoint_cache:
            endpoint_cache[v] = evaluate(points[v], _wct_expand((v,),graph,REFINE_RINGS+1))
        return endpoint_cache[v]
    return near, endpoint, evaluate



def _vertex_fan_witness(p0, f0, p1, f1, points, polygon, vertex_faces, graph, eps):
    """Unfold a unique one-face gap around a shared mesh vertex.

    This supplies evidence, not a guessed vertex side or an unconditional
    cut. Both crossed edges must be real polygon edges; the straight
    unfolded segment must cross them in order and inside their endpoints.
    Ambiguous routes, concave intermediate corners and degenerate geometry
    supply no evidence. Existing sign/multipassage guards still apply later.
    """
    first, last = polygon(f0), polygon(f1)
    shared = set(first) & set(last)
    if len(shared) != 1:
        return None
    vertex = next(iter(shared))
    def xyz(point):
        return np.array([point.x, point.y, point.z], dtype=float)
    center = xyz(points[vertex]);d0 = xyz(p0)-center;d1 = xyz(p1)-center
    r0, r1 = float(np.linalg.norm(d0)), float(np.linalg.norm(d1))
    if min(r0, r1) <= eps:
        return None
    def has_edge(poly, a, b):
        return any({x,y} == {a,b} for x,y in zip(poly,poly[1:]+poly[:1]))
    def angle(a, b):
        return math.acos(float(np.clip(np.dot(a,b)/(np.linalg.norm(a)*np.linalg.norm(b)), -1., 1.)))
    def cross(a, b):
        return float(a[0]*b[1]-a[1]*b[0])
    candidates = []
    for middle in vertex_faces.get(vertex, ()):
        if middle in (f0, f1):
            continue
        poly = polygon(middle)
        e0, e1 = set(first)&set(poly), set(poly)&set(last)
        if len(e0) != 2 or len(e1) != 2 or vertex not in e0 or vertex not in e1:
            continue
        a, b = next(iter(e0-{vertex})), next(iter(e1-{vertex}))
        if a == b or a not in graph[vertex] or b not in graph[vertex]:
            continue
        if not (has_edge(first,vertex,a) and has_edge(poly,vertex,a)
                and has_edge(poly,vertex,b) and has_edge(last,vertex,b)):
            continue
        ea, eb = xyz(points[a])-center, xyz(points[b])-center
        la, lb = float(np.linalg.norm(ea)), float(np.linalg.norm(eb))
        if min(la, lb) <= eps:
            continue
        # Reject a concave polygon corner instead of using its complementary
        # exterior angle as a fictitious route through the face.
        coords = [xyz(points[v]) for v in poly]
        normal = sum((np.cross(x,y) for x,y in zip(coords,coords[1:]+coords[:1])), np.zeros(3))
        k = poly.index(vertex)
        turn = np.cross(coords[(k+1)%len(poly)]-center,coords[k-1]-center)
        if float(np.dot(turn,normal)) <= 0.:
            continue
        theta, phi0, phi1 = angle(ea,eb), angle(ea,d0), angle(eb,d1)
        start = r0*np.array([math.cos(phi0),-math.sin(phi0)])
        end = r1*np.array([math.cos(theta+phi1),math.sin(theta+phi1)])
        delta = end-start;hits = []
        for other, direction, length in ((a,0.,la),(b,theta,lb)):
            ray = np.array([math.cos(direction),math.sin(direction)])
            den = cross(delta,ray)
            if abs(den) <= max(float(np.linalg.norm(delta)),1.)*1e-12:
                break
            t = -cross(start,ray)/den
            radial = float(np.dot(start+t*delta,ray))/length
            tolerance = eps/length
            if not (-tolerance <= t <= 1.+tolerance and -tolerance <= radial <= 1.+tolerance):
                break
            radial = max(0.,min(1.,radial))
            edge = tuple(sorted((vertex,other)))
            u = radial if edge[0] == vertex else 1.-radial
            hits.append((edge,u,t))
        if len(hits) == 2 and hits[0][2]+1e-10 < hits[1][2]:
            candidates.append(dict(vertex=vertex,face=int(middle),hits=hits))
    return candidates[0] if len(candidates) == 1 else None

def _curve_surface_witnesses(region, mesh, eps):
    """Independent geometric evidence: consecutive projected curve samples
    cross between two polygons which share an actual mesh edge.

    Unfold their two sample-edge triangles about that edge. The crossing
    coordinate is continuous even at a crease where a nearest-point tangent
    test has no point sufficiently close to the edge.
    """
    points, graph = region['points'], region['graph']
    polygon_cache = {}
    def polygon(fid):
        if fid not in polygon_cache:
            polygon_cache[fid] = list(map(int,mesh.getPolygonVertices(fid)))
        return polygon_cache[fid]
    witnesses = []
    vertex_faces = None
    region['field_fan_bridges'] = []
    def fan_faces():
        nonlocal vertex_faces
        if vertex_faces is None:
            vertex_faces = {}
            for fid in range(int(getattr(mesh, 'numPolygons', 0))):
                for vertex in polygon(fid):
                    vertex_faces.setdefault(vertex, []).append(fid)
        return vertex_faces
    for curve_id, name in enumerate(region['curves']):
        records = region['curve_samples'][name]
        by_edge = {}
        for i in range(len(records)):
            j=(i+1)%len(records)
            p0,f0,_=records[i]
            p1,f1,_=records[j]
            if f0==f1:
                continue
            shared=set(polygon(f0)) & set(polygon(f1))
            if len(shared) == 1:
                route = _vertex_fan_witness(p0,f0,p1,f1,points,polygon,fan_faces(),graph,eps)
                if route is not None:
                    for edge,u,_ in route['hits']:
                        by_edge.setdefault(edge,[]).append((float(u),i,j))
                    region['field_fan_bridges'].append(dict(curve=curve_id,samples=[i,j],
                        faces=[int(f0),route['face'],int(f1)],vertex=int(route['vertex']),
                        edges=[list(edge) for edge,_,_ in route['hits']]))
                continue
            if len(shared)!=2:
                continue
            a,b=sorted(shared)
            if b not in graph[a]:
                continue
            # A common pair of polygon vertices must be their shared edge,
            # not an internal diagonal of an n-gon or malformed topology.
            def has_edge(poly):
                return any({x,y}=={a,b} for x,y in zip(poly,poly[1:]+poly[:1]))
            if not has_edge(polygon(f0)) or not has_edge(polygon(f1)):
                continue
            edge=points[b]-points[a]
            length=edge.length()
            if length<=eps:
                continue
            unit=edge*(1./length)
            delta0,delta1=p0-points[a],p1-points[a]
            x0,x1=delta0*unit,delta1*unit
            d0=(delta0-unit*x0).length()
            d1=(delta1-unit*x1).length()
            if d0+d1<=eps:
                continue
            u=(x0*d1+x1*d0)/((d0+d1)*length)
            if -eps/length<=u<=1.+eps/length:
                by_edge.setdefault((a,b),[]).append((max(0.,min(1.,u)),i,j))
        witnesses.append(by_edge)
    if region['field_fan_bridges']:
        log.info('[CurveFan V17.6.2] bridges={}, faces={}'.format(
            len(region['field_fan_bridges']),[r['faces'] for r in region['field_fan_bridges'][:8]]))
    return witnesses


def _recover_witness_cuts(a,b,region,data,existing,eps):
    """Recover ONLY a missing, independently witnessed sign crossing.

    No model/vertex IDs are hard coded. The old proximity guard and the
    incomplete-ridge guard stay in place. No existing accepted cut is moved.
    """
    recovered=[]
    witnesses=region.get('curve_edge_witnesses',[])
    for ci,(_,endpoint,_) in enumerate(data):
        value=(.5,1.,0.)[ci]
        if any(abs(q-value)<1e-8 for _,q in existing):
            continue
        candidates=witnesses[ci].get((a,b),[]) if ci<len(witnesses) else []
        if not candidates:
            continue
        ra,rb=endpoint(a),endpoint(b)
        if ra is None or rb is None or ra[0]*rb[0]>=0:
            continue
        # Multiple passages through one edge are geometrically ambiguous;
        # never turn them into an arbitrary repair.
        length=region['graph'][a][b]
        distinct=[]
        for u,i,j in candidates:
            if not any(abs(u-old[0])*length<=eps*4 for old in distinct):
                distinct.append((u,i,j))
        if len(distinct)!=1:
            continue
        u,i,j=distinct[0]
        recovered.append((u,value))
        region.setdefault('field_witness_recoveries',[]).append(
            dict(edge=[int(a),int(b)],curve=int(ci),u=float(u),samples=[int(i),int(j)]))
    return recovered


def _edge_cuts(a, b, region, data, eps):
    """Place curve constraints inside existing edges instead of snapping a
    whole vertex ring to the curve's value. Multiple curves can cut one edge.
    """
    graph, points = region['graph'], region['points']
    pa, pb = points[a], points[b]
    delta = pb-pa
    length = delta.length()
    local = _wct_expand((a,b), graph, REFINE_RINGS+1)
    cuts = []
    fixed_values = (0.5, 1.0, 0.0)  # Ridge, FWD, BWD.
    for curve_id, (near, endpoint, evaluate) in enumerate(data):
        if a not in near and b not in near:
            continue
        value = fixed_values[curve_id]
        ra, rb = endpoint(a), endpoint(b)
        if ra is not None and ra[1] <= eps:
            cuts.append((0., value))
        if rb is not None and rb[1] <= eps:
            cuts.append((1., value))
        # Probe long/curved edges as well: same endpoint signs do not rule out
        # two crossings. Membership itself is never inferred from these cuts.
        steps = max(4,min(32,int(math.ceil(length/max(region['median']*.25,eps)))))
        probes = [(0.,ra)]
        for k in range(1,steps):
            u = k/float(steps)
            probes.append((u,evaluate(pa+delta*u,local)))
        probes.append((1.,rb))
        for u,r in probes[1:-1]:
            if r is not None and r[1] <= eps:
                cuts.append((u,value))
        for (lo, left),(hi, right) in zip(probes,probes[1:]):
            if left is None or right is None or left[0]*right[0] >= 0:
                continue
            lv, rv = left[0],right[0]
            result = None
            for _ in range(32):
                mid=(lo+hi)*.5
                result=evaluate(pa+delta*mid,local)
                if result is None:
                    break
                if (hi-lo)*length <= eps or abs(result[0]) <= eps:
                    break
                if lv*result[0] <= 0:
                    hi,rv=mid,result[0]
                else:
                    lo,lv=mid,result[0]
            if result is not None and result[1] <= max(eps*10,region['median']*.025):
                cuts.append(((lo+hi)*.5,value))
    cuts.extend(_recover_witness_cuts(a,b,region,data,cuts,eps))
    cuts.sort()
    unique=[]
    for u,value in cuts:
        if unique and abs(unique[-1][0]-u)*length <= eps*4:
            if abs(unique[-1][1]-value)>1e-8:
                raise RuntimeError('Curve constraints intersect on mesh edge {}-{}'.format(a,b))
            continue
        unique.append((u,value))
    return unique

def _finish_native_cuts(a,b,region,data,raw_cuts,eps):
    length=region['graph'][a][b]
    cuts=list(raw_cuts)
    cuts.extend(_recover_witness_cuts(a,b,region,data,cuts,eps))
    cuts.sort()
    unique=[]
    for u,value in cuts:
        if unique and abs(unique[-1][0]-u)*length <= eps*4:
            if abs(unique[-1][1]-value)>1e-8:
                raise RuntimeError('Curve constraints intersect on mesh edge {}-{}'.format(a,b))
            continue
        unique.append((u,value))
    return unique




def _adaptive_ridge_witnesses(region,mesh,curve,eps,max_queries=192,max_depth=8):
    """Resolve skipped surface faces with ACTUAL curve/mesh evaluations.

    Only used after the unchanged incomplete-ridge guard failed. New witness
    cuts still need the original endpoint-sign and unique-passage checks.
    No shortest-route guess, vertex labels, or synthetic region expansion.
    """
    name=region['curves'][0]
    records=region['curve_samples'][name]
    points,graph=region['points'],region['graph']
    polygons={}
    def polygon(face):
        if face not in polygons:polygons[face]=list(map(int,mesh.getPolygonVertices(face)))
        return polygons[face]
    def edge_in(poly,a,b):
        return any({x,y}=={a,b} for x,y in zip(poly,poly[1:]+poly[:1]))
    def shared_edge(f0,f1):
        shared=set(polygon(f0))&set(polygon(f1))
        if len(shared)!=2:return None
        a,b=sorted(shared)
        return (a,b) if b in graph[a] and edge_in(polygon(f0),a,b) and edge_in(polygon(f1),a,b) else None
    try:
        start,end=map(float,curve.knotDomain)
    except Exception:
        return 0
    period=end-start
    if period<=0:return 0
    additions={};queries=0;observed=[];vertex_faces=None
    def add_short_fan(left,right,parent):
        nonlocal vertex_faces
        if len(set(polygon(left[1]))&set(polygon(right[1])))!=1:return
        if vertex_faces is None:
            vertex_faces={}
            for face in range(int(mesh.numPolygons)):
                for vertex in polygon(face):vertex_faces.setdefault(vertex,[]).append(face)
        route=_vertex_fan_witness(left[0],left[1],right[0],right[1],
                                  points,polygon,vertex_faces,graph,eps)
        if route is not None:
            for edge,u,_ in route['hits']:
                additions.setdefault(edge,[]).append((float(u),parent[0],parent[1]))
    def crossing(left,right,parent):
        p0,f0,_=left;p1,f1,_=right
        edge=shared_edge(f0,f1)
        if edge is None:return
        a,b=edge;delta=points[b]-points[a];length=delta.length()
        if length<=eps:return
        unit=delta*(1./length);v0,v1=p0-points[a],p1-points[a]
        x0,x1=v0*unit,v1*unit
        d0,d1=(v0-unit*x0).length(),(v1-unit*x1).length()
        if d0+d1<=eps:return
        u=(x0*d1+x1*d0)/((d0+d1)*length)
        if -eps/length<=u<=1.+eps/length:
            additions.setdefault(edge,[]).append((max(0.,min(1.,u)),parent[0],parent[1]))
    def visit(left,right,parent,depth):
        nonlocal queries
        if left[1]==right[1]:return
        if shared_edge(left[1],right[1]) is not None:
            crossing(left,right,parent);return
        if depth>=max_depth:
            # Closest-surface projection can jump over a sharp corner. Apply
            # the existing unique one-face unfolding only to the now-refined
            # interval; its original sign/multipassage checks still follow.
            add_short_fan(left,right,parent)
            return
        if queries>=max_queries:return
        param=(left[2]+right[2])*.5
        queries+=1
        point=curve.getPointAtParam(start+(param-start)%period,om.MSpace.kWorld)
        projected,face=mesh.getClosestPoint(point,om.MSpace.kWorld)
        middle=(projected,int(face),param)
        observed.append(dict(param=float(param),face=int(face),parent=list(parent),
                             point=[float(projected.x),float(projected.y),float(projected.z)]))
        visit(left,middle,parent,depth+1)
        visit(middle,right,parent,depth+1)
    for i,left in enumerate(records):
        j=(i+1)%len(records);right=records[j]
        if left[1]==right[1] or shared_edge(left[1],right[1]) is not None:continue
        right=(right[0],right[1],right[2]+period if right[2]<=left[2] else right[2])
        visit(left,right,(i,j),0)
    if not additions:return 0
    # Preserve existing witnesses. Ambiguous/multiple passages remain rejected
    # by _recover_witness_cuts; accepted original cuts are never moved.
    target=region['curve_edge_witnesses'][0]
    for edge,rows in additions.items():target.setdefault(edge,[]).extend(rows)
    region['field_adaptive_ridge_samples']=observed
    log.info('[RidgeAdaptive V17.8.10] queries={}, observed_faces={}, witness_edges={}'.format(
        queries,sorted(set(row['face'] for row in observed)),len(additions)))
    return len(additions)


def _repair_ridge_constraints(region,mesh,curves,data,edges,fixed,eps):
    """Retry only the known guard, preserving selection and all old cuts."""
    graph=region['graph']
    if not _adaptive_ridge_witnesses(region,mesh,curves[0],eps):return False
    # Reuse the original endpoint evaluators and their cached native values.
    changed=[];updated=[]
    for a,b,length,old in edges:
        extra=_recover_witness_cuts(a,b,region,data,old,eps)
        if not extra:
            updated.append((a,b,length,old));continue
        combined=sorted(list(old)+extra);unique=[]
        for u,value in combined:
            if unique and abs(unique[-1][0]-u)*length<=eps*4:
                if abs(unique[-1][1]-value)>1e-8:
                    raise RuntimeError('Curve constraints intersect on mesh edge {}-{}'.format(a,b))
                continue
            unique.append((u,value))
        for u,value in extra:
            vertex=a if u*length<=eps*4 else b if (1-u)*length<=eps*4 else None
            if vertex is not None:
                if vertex in fixed and abs(fixed[vertex]-value)>1e-8:
                    raise RuntimeError('Multiple curve values at vertex {}'.format(vertex))
                fixed[vertex]=value
        updated.append((a,b,length,unique));changed.append([int(a),int(b)])
    if not changed:return False
    edges[:]=updated
    log.info('[RidgeAdaptive V17.8.10] added_edges={}; strict membership unchanged'.format(changed))
    return True


def _solve_dirichlet(graph, edges, selected, fixed, count, eps, required=None):
    """Positive graph Laplacian with sub-edge Dirichlet constraints.

    There is no FWD/BWD hard partition and no per-side distance flood. Fixed
    ridge samples separate the two half-fields without deleting selected
    vertices. Components bounded by .5 and 1 stay in [.5,1] by the maximum
    principle; components bounded by 0 and .5 stay in [0,.5].
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.linalg import spsolve
    required=set(selected) if required is None else set(required)
    unknown=sorted(set(selected)-set(fixed))
    index={v:i for i,v in enumerate(unknown)}
    connections={v:{} for v in unknown}
    boundary={v:[] for v in unknown}
    def add_fixed(v,value,distance):
        if v in index:
            boundary[v].append((float(value),1./max(distance,eps)))
    for a,b,length,cuts in edges:
        if cuts:
            add_fixed(a,cuts[0][1],cuts[0][0]*length)
            add_fixed(b,cuts[-1][1],(1.-cuts[-1][0])*length)
        elif a in selected and b in selected:
            if b in fixed:add_fixed(a,fixed[b],length)
            if a in fixed:add_fixed(b,fixed[a],length)
            if a in index and b in index:
                weight=1./max(length,eps)
                connections[a][b]=weight
                connections[b][a]=weight
    # Guard against an incomplete ridge constraint, not against a guessed
    # per-vertex side. A component touching both 0 and 1 would leak across it.
    seen=set();ignored=set()
    for seed in unknown:
        if seed in seen:continue
        queue,values,group=[seed],[],[]
        seen.add(seed)
        while queue:
            v=queue.pop();group.append(v)
            values.extend(value for value,_ in boundary[v])
            for nb in connections[v]:
                if nb not in seen:seen.add(nb);queue.append(nb)
        if not (set(group) & required):
            ignored.update(group)
            continue
        if not values:
            raise RuntimeError('Curve field has no constraint for vertices {}'.format(group[:12]))
        if min(values)<.5-1e-8 and max(values)>.5+1e-8:
            raise RuntimeError('Ridge curve constraints are incomplete near vertices {}'.format(group[:12]))
    unknown=[v for v in unknown if v not in ignored]
    index={v:i for i,v in enumerate(unknown)}
    rows,cols,entries=[],[],[]
    rhs=np.zeros(len(unknown))
    for v in unknown:
        i=index[v]
        diagonal=sum(connections[v].values())+sum(w for _,w in boundary[v])
        rows.append(i);cols.append(i);entries.append(diagonal)
        for nb,w in connections[v].items():
            rows.append(i);cols.append(index[nb]);entries.append(-w)
        rhs[i]=sum(value*w for value,w in boundary[v])
    field=np.zeros(count)
    for v,value in fixed.items():field[v]=value
    if unknown:
        matrix=coo_matrix((entries,(rows,cols)),shape=(len(unknown),len(unknown))).tocsr()
        solved=np.asarray(spsolve(matrix,rhs)).reshape(-1)
        if not np.all(np.isfinite(solved)) or solved.min() < -1e-6 or solved.max() > 1+1e-6:
            raise RuntimeError('Invalid three-curve field solution')
        field[unknown]=np.clip(solved,0.,1.)
    return field


def weight_coordinates(region, mesh_name):
    """One continuous field: BWD=0, ridge=.5, FWD=1.

    Soft and IK are mappings of this same field. Membership and FWD exterior
    remain the independently verified strict region, unchanged by the solve.
    """
    clock = __import__('time').perf_counter
    started = stage = clock()
    region['field_query_counts'] = {}
    mesh=om.MFnMesh(_wct_shape(mesh_name,'mesh'))
    curves=[om.MFnNurbsCurve(_wct_shape(name,'nurbsCurve')) for name in region['curves']]
    count=len(region['points'])
    eps=max(region['median']*STRICT_EPS_FACTOR,1e-10)
    selected=set(np.flatnonzero(region['mask']))
    backend = globals().get('_native_cut_backend')
    native = backend(region,mesh_name,eps) if backend else None
    data=[_curve_edge_data(region,mesh,fn,name,eps) for fn,name in zip(curves,region['curves'])]
    region['curve_edge_witnesses']=_curve_surface_witnesses(region,mesh,eps)
    region['field_witness_recoveries']=[]
    setup_time = clock()-stage
    stage = clock()
    edges=[];fixed={}
    # Numeric diagnostic inputs can be exported by the optional preview
    # script, including a partial case if native geometry evaluation fails.
    region['field_edges']=edges
    region['field_fixed']=fixed
    for a in range(count):
        for b,length in region['graph'][a].items():
            if b<=a:continue
            relevant=any(a in near or b in near for near,_,_ in data)
            if not relevant:
                cuts=[]
            elif native is None:
                cuts=_edge_cuts(a,b,region,data,eps)
            else:
                cuts=_finish_native_cuts(a,b,region,data,native.get((a,b),()),eps)
            for u,value in cuts:
                v=a if u*length<=eps*4 else b if (1-u)*length<=eps*4 else None
                if v is not None:
                    if v in fixed and abs(fixed[v]-value)>1e-8:
                        raise RuntimeError('Multiple curve values at vertex {}'.format(v))
                    fixed[v]=value
            edges.append((a,b,length,cuts))
    # Selection is a write gate, not the domain of the numerical solver.
    # Preserve auxiliary original vertices so uncertain/excluded boundary
    # vertices cannot sever a valid surface route. Curve constraints, not
    # guessed vertex labels, stop propagation across the three curves.
    recoveries=region['field_witness_recoveries']
    log.info('[CurveConstraints V17.3.5] recovered={}, edges={}'.format(
        len(recoveries),[r['edge'] for r in recoveries[:12]]))
    intersection_time = clock()-stage
    stage = clock()
    try:
        field=_solve_dirichlet(region['graph'],edges,set(range(count)),fixed,count,eps,
                               required=selected)
    except RuntimeError as problem:
        if not str(problem).startswith('Ridge curve constraints are incomplete'):
            raise
        if not _repair_ridge_constraints(region,mesh,curves,data,edges,fixed,eps):
            raise
        # The same guard runs again. Failed or ambiguous repairs still cancel.
        field=_solve_dirichlet(region['graph'],edges,set(range(count)),fixed,count,eps,
                               required=selected)
    solve_time = clock()-stage
    region['decay_field']=field.copy()
    t=np.zeros(count);side=np.zeros(count,np.int8);ridge=np.zeros(count,bool)
    for v in selected:
        q=field[v]
        t[v]=min(1.,abs(2.*q-1.))
        side[v]=1 if q>=.5 else -1
        ridge[v]=v in fixed and abs(fixed[v]-.5)<=1e-8
    log.info('[ThreeCurveField V17.3.5] strict={}, constraints_on_edges={}, ridge_vertices={}, no_hard_partition=True'.format(
        len(selected),sum(bool(e[3]) for e in edges),int(ridge.sum())))
    log.info('[WCT 衰减内部耗时] 准备={:.3f}s 交点={:.3f}s 线性求解={:.3f}s 总计={:.3f}s 查询={}'.format(
        setup_time, intersection_time, solve_time, clock()-started,
        region['field_query_counts']))
    return t,side,ridge
