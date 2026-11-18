import ast,os,statistics,contextlib,io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
class ProjectionAuditFlowTests(unittest.TestCase):
 def run_audit(self,bad_fast=False,bad_legacy=False,baseline_failure=False):
  clock=[0.];saved=[];cleared=[]
  P=SimpleNamespace(_BATCHES=0,_FALLBACKS=0,binary_identity=lambda:dict(version='1.8.1',sha256='binarytest'),remember_audit=lambda mode:saved.append(mode),clear_audit=lambda:cleared.append(1))
  class Surface:
   def project_to_surface(self,points,use_mesh_api=True):
    native=os.environ.get('WCT_NATIVE_PROJECT')=='1';mode=int(os.environ.get('WCT_NATIVE_PROJECT_MODE','0'))
    if baseline_failure and not native:raise RuntimeError('projection source failure')
    clock[0]+=(.25 if mode==0 else .01) if native else .2
    result=np.asarray(points,dtype=float).copy()
    if native:
     P._BATCHES+=1
     if (bad_fast and mode==2) or (bad_legacy and mode==0):result[0,0]+=1e-12
    return result
  surface=Surface()
  raw=np.array([[0.,0.,1.],[1.,0.,1.],[0.,1.,1.]])
  builder=SimpleNamespace(rest_surface=surface,display_mode='laplacian',internal_per_segment=2,
   _read_joint_rest_positions=lambda *a:raw.copy(),_boundary_points=lambda *a:None,
   _build_path_laplacian=lambda *a:(surface.project_to_surface(np.repeat(raw,20,axis=0)),[0,20,40,59]),
   _apply_offset=lambda shape,points,center:points)
  CR=SimpleNamespace(clear_mesh_fn_cache=lambda:None,simplify_by_anchors=lambda dense,*a:np.asarray(dense)[[0,20,40,59]],fit_circle_3d=lambda *a:None,ep_to_cv=lambda p:np.asarray(p))
  ctx=SimpleNamespace(rest_surface=surface,curve_builder=builder,continuous_mesh='mesh',active_closed=True,group_mgr=SimpleNamespace(get_joints_from_active=lambda kind:['a','b','c']))
  ns=dict(os=os,time=SimpleNamespace(perf_counter=lambda:clock[0]),statistics=statistics,np=np,cmds=SimpleNamespace(listRelatives=lambda *a,**kw:['shape']),core=SimpleNamespace(_ctx=ctx),CR=CR,P=P,_loc_sort_key=lambda name:name)
  tree=ast.parse((ROOT/'python/maya_audit_native_projection.py').read_text());tree.body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
  previous=dict(os.environ)
  if baseline_failure:
   with self.assertRaisesRegex(RuntimeError,'projection source failure'):
    with contextlib.redirect_stdout(io.StringIO()):exec(compile(tree,'audit','exec'),ns)
  else:
   with contextlib.redirect_stdout(io.StringIO()):exec(compile(tree,'audit','exec'),ns)
  self.assertEqual(dict(os.environ),previous);self.assertNotIn('project_to_surface',surface.__dict__)
  return saved,cleared,ns
 def test_exact_fast_mode_is_selected_and_temporary_observer_restored(self):
  with patch.dict(os.environ,{},clear=True):
   saved,cleared,ns=self.run_audit();self.assertEqual(saved,[2]);self.assertFalse(cleared)
 def test_even_tiny_coordinate_change_rejects_fast_mode(self):
  with patch.dict(os.environ,{},clear=True):
   saved,cleared,ns=self.run_audit(bad_fast=True);self.assertFalse(saved);self.assertEqual(cleared,[1]);self.assertFalse(ns['results'][1]['passed'])
 def test_legacy_mismatch_blocks_fast_mode(self):
  with patch.dict(os.environ,{},clear=True):
   saved,cleared,ns=self.run_audit(bad_legacy=True);self.assertFalse(saved);self.assertEqual(cleared,[1])
 def test_original_failure_restores_observer_and_propagates(self):
  with patch.dict(os.environ,{},clear=True):
   saved,cleared,ns=self.run_audit(baseline_failure=True);self.assertFalse(saved)
class SurfaceSourceGuardTests(unittest.TestCase):
 def test_only_project_to_surface_has_changed_and_legacy_math_is_identical(self):
  def methods(path):
   cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='RestPoseSurface')
   return {n.name:n for n in cls.body if isinstance(n,ast.FunctionDef)}
  before=methods(ROOT/'tests/reference_native/RestPoseSurface_before.py');after=methods(ROOT/'weightCurveTool/tools/RestPoseSurface.py')
  for name in before:
   if name=='project_to_surface':
    node=after[name];i=next(i for i,n in enumerate(node.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='backend' for t in n.targets))
    self.assertIsInstance(node.body[i+1],ast.If);del node.body[i:i+2]
   self.assertEqual(ast.dump(before[name]),ast.dump(after[name]),name)
if __name__=='__main__':unittest.main()
