"""Preserve the reviewed GitHub cleanup while adding native projection."""
import ast
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GitHubIntegrationTests(unittest.TestCase):
    def test_unmodified_runtime_matches_reviewed_commit_byte_for_byte(self):
        manifest = json.loads((ROOT / 'tests/reference_native/github_baseline.json').read_text())
        runtime = ROOT.parents[1]
        changed = set(manifest['changed_runtime_files'])
        added = set(manifest['added_runtime_files'])
        baseline = manifest['baseline_runtime_sha256']
        runtime_files = (*runtime.glob('*.py'), *(runtime / 'tools').rglob('*.py'))
        actual = {p.relative_to(runtime).as_posix() for p in runtime_files}
        self.assertEqual(actual, set(baseline) | added | {'start_weight_curve_tool.py'})
        for relative, expected in baseline.items():
            if relative in changed:
                continue
            with self.subTest(file=relative):
                self.assertEqual(
                    hashlib.sha256((runtime / relative).read_bytes()).hexdigest(),
                    expected,
                )

    def test_repository_cache_validation_and_diagnostics_remain(self):
        before = ast.parse((ROOT / 'tests/reference_native/RestPoseSurface_before.py').read_text())
        after = ast.parse((ROOT.parents[1] / 'tools/RestPoseSurface.py').read_text())

        def methods(tree):
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                       and n.name == 'RestPoseSurface')
            return {n.name: ast.dump(n) for n in cls.body
                    if isinstance(n, ast.FunctionDef)}

        old, new = methods(before), methods(after)
        self.assertEqual(old.keys(), new.keys())
        for name in ('_build', '_embedding_fingerprint', '_read_cached_embedding',
                     '_write_cached_embedding', 'get_joint_rest_position'):
            with self.subTest(method=name):
                self.assertEqual(old[name], new[name])

    def test_build_and_installer_target_same_native_version(self):
        for name in ('build_maya2024.cmd', 'install_maya2024.cmd'):
            with self.subTest(file=name):
                self.assertIn('.wct_build_1_8_2.ok', (ROOT / name).read_text())
        self.assertIn('"1.8.2"', (ROOT / 'src/WctSurfaceCurveNode.cpp').read_text())


if __name__ == '__main__':
    unittest.main()
