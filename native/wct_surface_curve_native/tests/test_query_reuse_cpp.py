from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QueryReuseCppTests(unittest.TestCase):
    def test_original_traversal_remains_verbatim_for_disabled_mode(self):
        before = (ROOT / 'tests/reference_native/before_1_8_2_FaceBoundsBVH.h').read_text()
        after = (ROOT / 'src/FaceBoundsBVH.h').read_text()
        original = before.split(' template<class Query> Hit closest(')[1].rsplit('};', 1)[0].strip()
        retained = after.split(' template<class Query> Hit closest(')[1].split('// Best-first broad phase')[0].strip()
        self.assertEqual(original, retained)

    def test_seeded_bvh_and_bit_exact_command_cache(self):
        old = (ROOT / 'tests/reference_native/before_1_8_2_FaceBoundsBVH.h').read_text()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / 'legacy_face_bounds.h').write_text(
                old.replace('namespace wctface {', 'namespace legacy_wctface {'))
            binary = directory / 'queries'
            subprocess.run([
                'g++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                '-I' + str(ROOT / 'src'), '-I' + str(directory),
                str(ROOT / 'tests/query_reuse_test.cpp'), '-o', str(binary),
            ], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
