from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class PreservedNativeTests(unittest.TestCase):
 def test_curve_evaluation_math_is_unchanged_from_1_8_1(self):
  old=(ROOT/'tests/reference_native/before_1_8_2_CurveEdgeCutsCommand.cpp').read_text()
  new=(ROOT/'src/CurveEdgeCutsCommand.cpp').read_text()
  before=old.split('struct Curve {')[1].split('MStatus WctCurveEdgeCuts::doIt')[0]
  after=new.split('struct Curve {')[1].split('MStatus WctCurveEdgeCuts::doIt')[0]
  self.assertEqual(before,after)
 def test_strict_query_command_is_unchanged_from_1_8_0(self):
  old=(ROOT/'tests/reference_native/CurveEdgeCuts_1_8_0.cpp').read_text().split('MStatus WctStrictBoundaryQueries::doIt')[1]
  new=(ROOT/'src/CurveEdgeCutsCommand.cpp').read_text().split('MStatus WctStrictBoundaryQueries::doIt')[1].split('MStatus WctProjectPoints::doIt')[0]
  self.assertEqual(old.strip(),new.strip())
 def test_curve_node_compute_remains_unchanged(self):
  old=(ROOT/'tests/reference_native/WctSurfaceCurveNode_before.cpp').read_text().split('#ifdef _WIN32\n#define WCT_EXPORT')[0]
  new=(ROOT/'src/WctSurfaceCurveNode.cpp').read_text().split('#ifdef _WIN32\n#define WCT_EXPORT')[0].replace('#include "StrictBoundaryQueryCommand.h"\n','').replace('#include "ProjectPointsCommand.h"\n','')
  self.assertEqual(old,new)
