import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QueryReuseDriverTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.identity = dict(version='1.8.2', sha256='compiled-binary')
        self.preferences = {}

        def option(**kwargs):
            if 'exists' in kwargs:
                return kwargs['exists'] in self.preferences
            if 'q' in kwargs:
                return self.preferences[kwargs['q']]
            if 'sv' in kwargs:
                self.preferences[kwargs['sv'][0]] = kwargs['sv'][1]
            if 'remove' in kwargs:
                self.preferences.pop(kwargs['remove'], None)

        path = ROOT / 'weightCurveTool/tools/NativeQueryReuse.py'
        tree = ast.parse(path.read_text())
        tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
        self.ns = dict(json=json, os=os, cmds=SimpleNamespace(optionVar=option),
                       binary_identity=lambda: dict(self.identity))
        exec(compile(tree, str(path), 'exec'), self.ns)

    def test_default_off_even_with_environment_request(self):
        self.assertEqual(self.ns['mode_if_enabled'](), 0)
        os.environ['WCT_NATIVE_QUERY_REUSE'] = '1'
        self.assertEqual(self.ns['mode_if_enabled'](), 0)

    def test_saved_mode_requires_same_binary_and_profile(self):
        self.ns['remember_audit']()
        self.assertEqual(self.ns['mode_if_enabled'](), 1)
        self.identity['sha256'] = 'replacement'
        self.assertEqual(self.ns['mode_if_enabled'](), 0)
        self.identity['sha256'] = 'compiled-binary'
        os.environ['WCT_NATIVE_ACCEL_NORMAL'] = '1'
        self.assertEqual(self.ns['mode_if_enabled'](), 0)

    def test_old_plugin_and_explicit_disable_keep_original(self):
        self.ns['remember_audit']()
        os.environ['WCT_NATIVE_QUERY_REUSE'] = '0'
        self.assertEqual(self.ns['mode_if_enabled'](), 0)
        os.environ.pop('WCT_NATIVE_QUERY_REUSE')
        self.identity['version'] = '1.8.1'
        self.assertEqual(self.ns['mode_if_enabled'](), 0)
        with self.assertRaises(RuntimeError):
            self.ns['remember_audit']()

    def test_audit_override_is_temporary_and_version_checked(self):
        os.environ.update(WCT_NATIVE_QUERY_REUSE_AUDIT='1', WCT_NATIVE_QUERY_REUSE='1')
        self.assertEqual(self.ns['mode_if_enabled'](), 1)
        self.identity['version'] = '1.8.1'
        self.assertEqual(self.ns['mode_if_enabled'](), 0)

    def test_corrupt_record_and_clear_disable_mode(self):
        self.preferences[self.ns['PREFERENCE']] = '{invalid'
        self.assertEqual(self.ns['mode_if_enabled'](), 0)
        self.ns['remember_audit']()
        self.ns['clear_audit']()
        self.assertEqual(self.ns['mode_if_enabled'](), 0)


if __name__ == '__main__':
    unittest.main()
