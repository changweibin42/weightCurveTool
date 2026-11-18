# -*- coding: utf-8 -*-
"""Read-only comparison of native query reuse; writes passing preferences only."""
import copy
import os
import statistics
import time
import numpy as np
from weightCurveTool import core
from weightCurveTool.tools import StrictCurveRegion as S
from weightCurveTool.tools import NativeEdgeCuts as N
from weightCurveTool.tools import NativeQueryReuse as Q
from weightCurveTool.tools.WeightColorPipeline import _active_boundary_curve_names

identity = Q.binary_identity()
if not Q._supported(identity):
    raise RuntimeError('Build/install plugin 1.8.2, restart Maya, then run this audit')
if not N._prepare_backend():
    raise RuntimeError('Native edge cuts are disabled or unavailable')
if os.environ.get('WCT_NATIVE_FACE_QUERY') != '1':
    raise RuntimeError('Query reuse requires the existing face-query backend')
ctx = core._ctx
mesh = ctx.continuous_mesh or ctx.group_mgr.find_mesh_from_active()
curves = _active_boundary_curve_names(ctx)
if not mesh or not curves or len(curves) != 3:
    raise RuntimeError('Activate the complete main/FWD/BWD curve set first')
region = S.build_strict_region(mesh, tuple(curves))
eps = max(region['median'] * S.STRICT_EPS_FACTOR, 1e-10)
keys = ('WCT_NATIVE_QUERY_REUSE', 'WCT_NATIVE_QUERY_REUSE_AUDIT',
        'WCT_NATIVE_CURVE_CACHE')
previous = {key: os.environ.get(key) for key in keys}
Q.clear_audit()
results = []
completed = False


def compare_raw(expected, actual, label):
    if expected == actual:
        return
    differences = []
    for edge in sorted(set(expected[0]) | set(actual[0])):
        if expected[0].get(edge) != actual[0].get(edge):
            differences.append(('edge', edge, expected[0].get(edge), actual[0].get(edge)))
            if len(differences) >= 6:
                break
    if not differences:
        for curve in expected[1]:
            a, b = expected[1][curve], actual[1][curve]
            for vertex in sorted(set(a) | set(b)):
                if a.get(vertex) != b.get(vertex):
                    differences.append(('endpoint', curve, vertex, a.get(vertex), b.get(vertex)))
                    if len(differences) >= 6:
                        break
    raise RuntimeError('{}: raw constraints changed: {}'.format(label, differences[:6]))


def raw(mode, mask=7):
    start = time.perf_counter()
    result = N._batch(region, mesh, eps, mask=mask, partial=True, query_reuse=mode)
    return result, time.perf_counter() - start


def snapshot():
    value = S.weight_coordinates(region, mesh)
    if not region.get('field_native_backend_used'):
        raise RuntimeError('Native command fell back to Python')
    return (copy.deepcopy(region['field_edges']), dict(region['field_fixed']),
            region['decay_field'].copy(), tuple(np.asarray(v).copy() for v in value))


def field(mode):
    os.environ['WCT_NATIVE_QUERY_REUSE_AUDIT'] = '1'
    os.environ['WCT_NATIVE_QUERY_REUSE'] = str(mode)
    os.environ['WCT_NATIVE_CURVE_CACHE'] = '0'
    N.clear_cache()
    return snapshot()


def compare_field(reference, candidate):
    if reference[:2] != candidate[:2]:
        raise RuntimeError('Field constraints changed')
    if not np.array_equal(reference[2], candidate[2]):
        raise RuntimeError('Solved decay field changed')
    if not all(np.array_equal(a, b) for a, b in zip(reference[3], candidate[3])):
        raise RuntimeError('Decay coordinates/side/ridge labels changed')


def restore_environment():
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


try:
    timings = {0: [], 1: []}
    # Warm-up both paths; each raw command owns a fresh, independent cache.
    expected, _ = raw(0)
    actual, _ = raw(1)
    compare_raw(expected, actual, 'warm-up')
    for iteration in range(5):
        for mode in ((0, 1) if iteration % 2 == 0 else (1, 0)):
            actual, seconds = raw(mode)
            compare_raw(expected, actual, 'mode={}'.format(mode))
            timings[mode].append(seconds)
    # Real dragging uses partial masks: validate all three paths independently.
    for index in range(3):
        mask = 1 << index
        old, _ = raw(0, mask)
        new, _ = raw(1, mask)
        compare_raw(old, new, 'partial mask={}'.format(mask))
    reference = field(0)
    candidate = field(1)
    compare_field(reference, candidate)
    for mode in (0, 1):
        row = dict(mode=mode, passed=True, median=statistics.median(timings[mode]),
                   trials=timings[mode])
        results.append(row)
        print('[WCT Query Reuse Audit] mode={} PASS median={:.4f}s'.format(mode, row['median']))
    old_seconds, new_seconds = results[0]['median'], results[1]['median']
    if new_seconds < old_seconds * .97:
        Q.remember_audit()
        message = '[WCT Query Reuse Audit] SELECTED reuse=1, gain={:.1f}%, binary={}'.format(
            100. * (1. - new_seconds / old_seconds), identity['sha256'][:12])
    else:
        Q.clear_audit()
        message = '[WCT Query Reuse Audit] Original path retained: gain below 3%'
    # Validate real cached execution too, then leave the selected exact-input
    # caches warm. No scene weights/curves are written by weight_coordinates.
    restore_environment()
    N.clear_cache()
    if os.environ.get('WCT_NATIVE_CURVE_CACHE', '1') == '1':
        for phase in ('cold', 'warm'):
            compare_field(reference, snapshot())
            print('[WCT Query Reuse Cache] {} PASS'.format(phase))
    print(message)
    completed = True
finally:
    if not completed:
        Q.clear_audit()
        N.clear_cache()
    restore_environment()
print('[WCT Query Reuse Audit END] No curves/weights/selection changed; results are in results.')
