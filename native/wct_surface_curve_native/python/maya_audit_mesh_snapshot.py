# -*- coding: utf-8 -*-
"""Read-only point-only prototype; normals always come from the original DAG.

Run in Maya 2024 with an active weightCurveTool three-curve set. No plugin
build or tool installation is required. This diagnostic never enables a
runtime mode, writes weights, creates DAG nodes, or changes selection.
"""
import contextlib
import hashlib
import statistics
import time
import numpy as np
import maya.api.OpenMaya as om
from weightCurveTool import core
from weightCurveTool.tools import StrictCurveRegion as S
from weightCurveTool.tools.WeightColorPipeline import _active_boundary_curve_names

MAX_CAPTURE = 100000
REPLAY_PER_KIND = 2048
REPLAY_ROUNDS = 3
HOOKS = ('om', '_native_cut_backend', '_prepare_region_mesh',
         '_prepare_boundary_queries', '_cached_curve_samples')
REPLACE_KINDS = ('point',)


def xyz_array(values):
    return np.asarray([(v.x, v.y, v.z) for v in values], dtype=np.float64).reshape(-1, 3)


def geometry(fn, space):
    counts, vertices = fn.getVertices()
    tri_counts, triangles = fn.getTriangles()
    normal_counts, normal_ids = fn.getNormalIds()
    return dict(points=xyz_array(fn.getPoints(space)),
                counts=np.asarray(counts, dtype=np.int64),
                vertices=np.asarray(vertices, dtype=np.int64),
                tri_counts=np.asarray(tri_counts, dtype=np.int64),
                triangles=np.asarray(triangles, dtype=np.int64),
                normals=xyz_array(fn.getNormals(space)),
                normal_counts=np.asarray(normal_counts, dtype=np.int64),
                normal_ids=np.asarray(normal_ids, dtype=np.int64))


def same_geometry(a, b, check_normals=True):
    keys = [key for key in a if check_normals or key not in
            ('normals', 'normal_counts', 'normal_ids')]
    different = [key for key in keys if not np.array_equal(a[key], b[key])]
    if different:
        raise RuntimeError('Geometry mismatch: ' + ', '.join(different))
    if not all(np.all(np.isfinite(a[key])) for key in ('points', 'normals')):
        raise RuntimeError('Source geometry contains NaN/Inf')


def fingerprint(data):
    h = hashlib.blake2b(digest_size=20)
    for key in sorted(data):
        value = np.ascontiguousarray(data[key])
        h.update(key.encode('ascii'))
        h.update(str((value.shape, value.dtype.str)).encode('ascii'))
        h.update(value.tobytes())
    return h.hexdigest()


def matrix_array(path):
    matrix = path.inclusiveMatrix()
    return np.asarray([[matrix.getElement(i, j) for j in range(4)] for i in range(4)])


def query(fn, kind, point, space):
    if kind == 'point':
        value, face = fn.getClosestPoint(point, space)
    else:
        value, face = fn.getClosestNormal(point, space)
    return value, int(face)


class QueryMesh:
    """Keep original topology and any query kind that was not replaced."""
    def __init__(self, original, target, space, records, replace_kinds=('point', 'normal')):
        self.original, self.target, self.space = original, target, space
        self.records = records
        self.phase = 'region'
        self.replace_kinds = frozenset(replace_kinds)

    def __getattr__(self, name):
        return getattr(self.original, name)

    def _call(self, kind, point, space):
        if space != om.MSpace.kWorld:
            raise RuntimeError('Unexpected non-world query in current curve pipeline')
        if len(self.records) >= MAX_CAPTURE:
            raise RuntimeError('Query capture exceeds MAX_CAPTURE={}'.format(MAX_CAPTURE))
        fn, query_space = ((self.target, self.space) if kind in self.replace_kinds
                           else (self.original, om.MSpace.kWorld))
        result, face = query(fn, kind, point, query_space)
        self.records.append((self.phase, kind, (float(point.x), float(point.y), float(point.z)),
                             (float(result.x), float(result.y), float(result.z)), face))
        return result, face

    def getClosestPoint(self, point, space):
        return self._call('point', point, space)

    def getClosestNormal(self, point, space):
        return self._call('normal', point, space)


class ApiView:
    """Patch this tool module only; never replace the global Maya API class."""
    def __init__(self, api, surface):
        self.api, self.surface = api, surface

    def __getattr__(self, name):
        return getattr(self.api, name)

    def MFnMesh(self, path):
        actual = self.api.MFnMesh(path)
        if actual.object() != self.surface.original.object():
            raise RuntimeError('Prototype encountered an unexpected mesh')
        return self.surface


@contextlib.contextmanager
def isolated_pipeline(module, api_view):
    absent = object()
    saved = {key: getattr(module, key, absent) for key in HOOKS}
    try:
        module.om = api_view
        for key in HOOKS[1:]:
            setattr(module, key, None)
        yield
    finally:
        for key, value in saved.items():
            if value is absent:
                delattr(module, key)
            else:
                setattr(module, key, value)


def pipeline(original, target, space, mesh, curves, replace_kinds=('point', 'normal')):
    records = []
    surface = QueryMesh(original, target, space, records, replace_kinds)
    start = time.perf_counter()
    with isolated_pipeline(S, ApiView(om, surface)):
        region = S.build_strict_region(mesh, curves)
        region_seconds = time.perf_counter() - start
        surface.phase = 'field'
        stage = time.perf_counter()
        values = S.weight_coordinates(region, mesh)
        field_seconds = time.perf_counter() - stage
    result = dict(mask=region['mask'].copy(), outer=region['fwd_outer'].copy(),
                  outer_error=region['outer_error'], uncertain=region['uncertain'],
                  edges=region['field_edges'], fixed=dict(region['field_fixed']),
                  field=region['decay_field'].copy(),
                  values=tuple(np.asarray(v).copy() for v in values))
    return result, records, dict(region=region_seconds, field=field_seconds)


def compare_pipeline(expected, actual):
    for key in ('mask', 'outer', 'field'):
        if not np.array_equal(expected[key], actual[key]):
            raise RuntimeError('Pipeline changed: ' + key)
    for key in ('outer_error', 'uncertain', 'edges', 'fixed'):
        if expected[key] != actual[key]:
            raise RuntimeError('Pipeline changed: ' + key)
    if not all(np.array_equal(a, b) for a, b in zip(expected['values'], actual['values'])):
        raise RuntimeError('Pipeline changed: decay coordinates/side/ridge labels')


def compare_trace(expected, actual):
    if len(expected) != len(actual):
        raise RuntimeError('Query count changed: {} -> {}'.format(len(expected), len(actual)))
    for index, (a, b) in enumerate(zip(expected, actual)):
        if a != b:
            raise RuntimeError('Query mismatch at {}: baseline={} candidate={}'.format(index, a, b))


def balanced_queries(records, kind):
    rows = [row for row in records if row[1] == kind]
    if len(rows) > REPLAY_PER_KIND:
        indices = np.linspace(0, len(rows)-1, REPLAY_PER_KIND, dtype=int)
        rows = [rows[int(i)] for i in indices]
    # Repeated identical inputs are retained: no quantization or de-duplication.
    return [(om.MPoint(*row[2]), row[3], row[4]) for row in rows]


def validate_queries(candidate, records, kinds=('point', 'normal')):
    for index, (_, kind, point, expected, expected_face) in enumerate(records):
        if kind not in kinds:
            continue
        value, face = query(candidate, kind, om.MPoint(*point), om.MSpace.kObject)
        if (float(value.x), float(value.y), float(value.z)) != expected or face != expected_face:
            raise RuntimeError('Query mismatch at {}: {} input={} expected={}/{} actual={}/{}'.format(
                index, kind, point, expected, expected_face,
                (float(value.x), float(value.y), float(value.z)), face))


def replay(fn, kind, rows, space):
    start = time.perf_counter()
    output = [query(fn, kind, point, space) for point, _, _ in rows]
    elapsed = time.perf_counter() - start
    for index, ((value, face), (_, expected, expected_face)) in enumerate(zip(output, rows)):
        if (float(value.x), float(value.y), float(value.z)) != expected or face != expected_face:
            raise RuntimeError('{} replay mismatch at {}'.format(kind, index))
    return elapsed


def benchmark(original, candidate, records, kinds=('point', 'normal')):
    report = {}
    for kind in kinds:
        rows = balanced_queries(records, kind)
        if not rows:
            continue
        trials = {'dag': [], 'candidate': []}
        for iteration in range(REPLAY_ROUNDS):
            for label in (('dag', 'candidate') if iteration % 2 == 0 else ('candidate', 'dag')):
                fn, space = ((original, om.MSpace.kWorld) if label == 'dag'
                             else (candidate, om.MSpace.kObject))
                trials[label].append(replay(fn, kind, rows, space))
        a, b = statistics.median(trials['dag']), statistics.median(trials['candidate'])
        report[kind] = dict(samples=len(rows), dag_seconds=a, candidate_seconds=b,
                            gain_percent=100.*(1.-b/a) if a else 0., trials=trials)
    return report


def main():
    ctx = core._ctx
    mesh = ctx.continuous_mesh or ctx.group_mgr.find_mesh_from_active()
    curves = _active_boundary_curve_names(ctx)
    if not mesh or not curves or len(curves) != 3:
        raise RuntimeError('Activate the complete main/FWD/BWD curve set first')
    path = S._wct_shape(mesh, 'mesh')
    original = om.MFnMesh(path)
    before = geometry(original, om.MSpace.kWorld)
    before_key = fingerprint(before)
    before_matrix = matrix_array(path)
    report = dict(mesh=mesh, fingerprint=before_key, candidates=[],
                  enabled=False, scope='Point-only Python prototype; not native runtime validation',
                  replaced_queries=list(REPLACE_KINDS), normal_source='original DAG mesh')
    print('[WCT Snapshot Audit] Point-only revision 2; normal_source=original_DAG')
    print('[WCT Snapshot Audit] Python capture timing is not native production timing')
    try:
        print('[WCT Snapshot Audit] Capturing complete Python range/field query sequence...')
        expected, records, baseline_time = pipeline(original, original, om.MSpace.kWorld, mesh, tuple(curves))
        report['captured'] = dict(point=sum(r[1]=='point' for r in records),
                                  normal=sum(r[1]=='normal' for r in records),
                                  pipeline_seconds=baseline_time)
        print('[WCT Snapshot Audit] Captured {}'.format(report['captured']))
        data, evaluated, data_error = None, None, None
        data_started = time.perf_counter()
        try:
            dependency = om.MFnDependencyNode(path.node())
            data = dependency.findPlug('worldMesh', False).elementByLogicalIndex(path.instanceNumber()).asMObject()
            evaluated = om.MFnMesh(data)
        except Exception as exc:
            data_error = repr(exc)
        data_seconds = time.perf_counter() - data_started
        for label in ('evaluated_data', 'independent_copy'):
            row = dict(mode=label, passed=False)
            try:
                start = time.perf_counter()
                if label == 'independent_copy':
                    owner = om.MFnMeshData().create()
                    candidate = om.MFnMesh()
                    # An identity DAG can be copied directly without changing
                    # coordinates/normals. Other transforms must first pass the
                    # evaluated-world-data geometry certificate.
                    if np.array_equal(before_matrix, np.eye(4)):
                        source = original.object()
                    else:
                        if evaluated is None:
                            raise RuntimeError('World data unavailable: ' + str(data_error))
                        same_geometry(before, geometry(evaluated, om.MSpace.kObject), check_normals=False)
                        source = data
                    copied = candidate.copy(source, owner)
                    if copied.isNull():
                        raise RuntimeError('Snapshot copy returned a null mesh')
                else:
                    if evaluated is None:
                        raise RuntimeError('World data unavailable: ' + str(data_error))
                    candidate = evaluated
                candidate_geometry = geometry(candidate, om.MSpace.kObject)
                # Point-only experiments do not consume the candidate normals.
                # Topology, coordinates and triangulation must still be exact.
                same_geometry(before, candidate_geometry, check_normals=False)
                row['normal_metadata_differs'] = any(
                    not np.array_equal(before[key], candidate_geometry[key])
                    for key in ('normals', 'normal_counts', 'normal_ids'))
                row['prepare_seconds'] = time.perf_counter() - start
                if label == 'evaluated_data' or not np.array_equal(before_matrix, np.eye(4)):
                    row['prepare_seconds'] += data_seconds
                print('[WCT Snapshot Audit] {} normal_metadata_differs={} normals_kept_on_DAG=True'.format(
                    label, row['normal_metadata_differs']))
                print('[WCT Snapshot Audit] {} validating every replaced point query...'.format(label))
                validate_queries(candidate, records, REPLACE_KINDS)
                actual, observed, timing = pipeline(original, candidate, om.MSpace.kObject,
                                                     mesh, tuple(curves), REPLACE_KINDS)
                compare_trace(records, observed)
                compare_pipeline(expected, actual)
                row.update(passed=True, exact_queries=len(records),
                           pipeline_seconds=timing,
                           replay=benchmark(original, candidate, records, REPLACE_KINDS))
                print('[WCT Snapshot Audit] {} PASS exact_queries={} prepare={:.4f}s'.format(
                    label, len(records), row['prepare_seconds']))
                for kind, result in row['replay'].items():
                    print('[WCT Snapshot Timing] {} {} samples={} DAG={:.4f}s data={:.4f}s gain={:.1f}%'.format(
                        label, kind, result['samples'], result['dag_seconds'],
                        result['candidate_seconds'], result['gain_percent']))
            except Exception as exc:
                row.update(passed=False, error=repr(exc))
                print('[WCT Snapshot Audit] {} FAIL {}'.format(label, repr(exc)))
            report['candidates'].append(row)
    finally:
        after = geometry(original, om.MSpace.kWorld)
        same_geometry(before, after)
        if not np.array_equal(matrix_array(path), before_matrix):
            raise RuntimeError('Source transform changed during read-only audit')
        if fingerprint(after) != before_key:
            raise RuntimeError('Source geometry fingerprint changed during read-only audit')
    print('[WCT Snapshot Audit END] No runtime mode enabled; no scene weights/curves/selection changed')
    print('[WCT Snapshot Audit] Report available as WCT_SNAPSHOT_REPORT')
    return report


WCT_SNAPSHOT_REPORT = main()
