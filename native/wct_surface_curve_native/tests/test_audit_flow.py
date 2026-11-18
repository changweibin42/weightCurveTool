import ast,contextlib,io,os,time,statistics
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
class AuditFlowTests(unittest.TestCase):
 def run_audit(self,bad_legacy=False,fail_baseline=False):
  clock=[0.];saved=[];cleared=[];contexts=[]
  def factory(*a,**kw):
   native=os.environ.get('WCT_NATIVE_STRICT_QUERIES')=='1';mode=int(os.environ.get('WCT_NATIVE_STRICT_MODE','0'))
   values={0:.25,1:.75}
   if native and (mode==2 or (bad_legacy and mode==0)):values[0]=.2
   return SimpleNamespace(rows=[('FWD',None,values,SimpleNamespace(native_vertices=2 if native else 0))])
  S=SimpleNamespace(_prepare_boundary_queries=factory,STRICT_EPS_FACTOR=1e-7)
  def build(*a):
   native=os.environ.get('WCT_NATIVE_STRICT_QUERIES')=='1';mode=int(os.environ.get('WCT_NATIVE_STRICT_MODE','0'))
   if fail_baseline and not native:raise RuntimeError('source geometry failed')
   clock[0]+=({0:.18,1:.10,2:.02,3:.08}[mode] if native else .20)
   S._prepare_boundary_queries()
   return dict(median=1.,mask=np.array([True,False]),fwd_outer=np.array([False,True]),decay_field=np.array([.25,.75]),outer_error=None)
  S.build_strict_region=build;S.weight_coordinates=lambda *a:(np.array([.5,.5]),np.array([-1,1]),np.array([False,False]))
  B=SimpleNamespace(prepare_queries=factory,clear_cache=lambda:None)
  N=SimpleNamespace(binary_identity=lambda:dict(sha256='testbinaryidentity',version='1.8.0'),remember_audit=lambda mode:saved.append(mode),clear_audit=lambda:cleared.append(1))
  tree=ast.parse((ROOT/'python/maya_audit_strict_boundary_queries.py').read_text());tree.body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
  ns=dict(os=os,time=SimpleNamespace(perf_counter=lambda:clock[0]),statistics=statistics,np=np,cmds=None,core=SimpleNamespace(_ctx=SimpleNamespace(continuous_mesh='mesh')),S=S,B=B,N=N,_active_boundary_curve_names=lambda ctx:['ridge','fwd','bwd'])
  previous=dict(os.environ)
  if fail_baseline:
   with self.assertRaisesRegex(RuntimeError,'source geometry failed'):
    with contextlib.redirect_stdout(io.StringIO()):exec(compile(tree,'audit','exec'),ns)
  else:
   with contextlib.redirect_stdout(io.StringIO()):exec(compile(tree,'audit','exec'),ns)
  self.assertEqual(dict(os.environ),previous);self.assertIs(S._prepare_boundary_queries,factory)
  return saved,cleared,ns
 def test_fastest_failed_mode_not_selected_and_environment_restored(self):
  with patch.dict(os.environ,{},clear=True):
   saved,cleared,ns=self.run_audit();self.assertEqual(saved,[3]);self.assertFalse(cleared)
   self.assertFalse(next(r for r in ns['results'] if r['mode']==2)['passed'])
 def test_legacy_failure_disables_all_native_candidates(self):
  with patch.dict(os.environ,{},clear=True):
   saved,cleared,ns=self.run_audit(bad_legacy=True);self.assertFalse(saved);self.assertEqual(cleared,[1])
 def test_original_geometry_error_still_propagates_and_restores_observer(self):
  with patch.dict(os.environ,{},clear=True):
   saved,cleared,ns=self.run_audit(fail_baseline=True);self.assertFalse(saved)
if __name__=='__main__':unittest.main()
