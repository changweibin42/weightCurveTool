import ast
import contextlib
import copy
import io
import os
from pathlib import Path
import statistics
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


class QueryReuseAuditTests(unittest.TestCase):
    def run_audit(self, slow=False, bad_raw=False, bad_partial=False, bad_field=False, bad_cache=False):
        clock = [0.]
        state = {'saved': False}
        identity = dict(version='1.8.2', sha256='test-binary')

        def clear():
            state['saved'] = False

        def batch(region, mesh, eps, mask, partial, query_reuse):
            clock[0] += (2.5 if slow else 1.) if query_reuse else 2.
            value = .5
            if query_reuse and (bad_raw or (bad_partial and mask == 2)):
                value += 1e-12
            return {(0, 1): [(value, .5)]}, {'ridge': {0: (.2, 3.)}, 'fwd': {}, 'bwd': {}}

        def field(region, mesh):
            corrupted = ((bad_field and os.environ.get('WCT_NATIVE_QUERY_REUSE') == '1')
                         or (bad_cache and os.environ.get('WCT_NATIVE_CURVE_CACHE', '1') == '1'))
            value = 1e-12 if corrupted else 0.
            region.update(field_native_backend_used=True, field_edges=[(0, 1, 1., [(0.5, 0.5)])],
                          field_fixed={0: 0.}, decay_field=np.array([value, 1.]))
            return np.array([value, 1.]), np.array([-1, 1]), np.array([False, False])

        region = dict(median=1.)
        namespace = dict(copy=copy, os=os, statistics=statistics, np=np,
                         time=SimpleNamespace(perf_counter=lambda: clock[0]),
                         core=SimpleNamespace(_ctx=SimpleNamespace(continuous_mesh='mesh')),
                         S=SimpleNamespace(build_strict_region=lambda *a: region,
                                           STRICT_EPS_FACTOR=1e-7, weight_coordinates=field),
                         N=SimpleNamespace(_prepare_backend=lambda: True, _batch=batch,
                                           clear_cache=lambda: None),
                         Q=SimpleNamespace(binary_identity=lambda: identity,
                                           _supported=lambda x: True, clear_audit=clear,
                                           remember_audit=lambda: state.update(saved=True)),
                         _active_boundary_curve_names=lambda ctx: ('ridge', 'fwd', 'bwd'))
        path = ROOT / 'python/maya_audit_query_reuse.py'
        tree = ast.parse(path.read_text())
        tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        previous = dict(os.environ)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(tree, str(path), 'exec'), namespace)
        finally:
            self.assertEqual(dict(os.environ), previous)
            self.assertEqual(state['saved'], not (slow or bad_raw or bad_partial or bad_field or bad_cache))
        return namespace

    def test_matching_faster_mode_is_saved_and_environment_restored(self):
        with patch.dict(os.environ, {'WCT_NATIVE_FACE_QUERY': '1'}, clear=True):
            self.run_audit()

    def test_matching_slower_mode_is_not_saved(self):
        with patch.dict(os.environ, {'WCT_NATIVE_FACE_QUERY': '1'}, clear=True):
            self.run_audit(slow=True)

    def test_even_tiny_raw_difference_rejects_mode(self):
        with patch.dict(os.environ, {'WCT_NATIVE_FACE_QUERY': '1'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'raw constraints changed'):
                self.run_audit(bad_raw=True)

    def test_each_partial_mask_must_match(self):
        with patch.dict(os.environ, {'WCT_NATIVE_FACE_QUERY': '1'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'partial mask=2'):
                self.run_audit(bad_partial=True)

    def test_even_tiny_solved_field_difference_rejects_mode(self):
        with patch.dict(os.environ, {'WCT_NATIVE_FACE_QUERY': '1'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'Solved decay field changed'):
                self.run_audit(bad_field=True)

    def test_cached_execution_failure_clears_the_provisional_record(self):
        with patch.dict(os.environ, {'WCT_NATIVE_FACE_QUERY': '1'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'Solved decay field changed'):
                self.run_audit(bad_cache=True)


if __name__ == '__main__':
    unittest.main()
