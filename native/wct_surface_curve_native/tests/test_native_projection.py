import ast,os,json,contextlib,io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import numpy as np
from synthetic_curve_scenes import LOG
ROOT=Path(__file__).resolve().parents[1]
class NativeProjectionTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.pref={};self.calls=[];self.version='1.8.1';self.sha='binary1'
  def identity():return dict(version=self.version,sha256=self.sha)
  def option(**kw):
   if 'exists' in kw:return kw['exists'] in self.pref
   if 'q' in kw:return self.pref[kw['q']]
   if 'sv' in kw:self.pref[kw['sv'][0]]=kw['sv'][1]
   if 'remove' in kw:self.pref.pop(kw['remove'],None)
  def command(mesh,mode,payload):
   self.calls.append((mesh,mode,payload));words=payload.split();n=int(words[1]);points=np.array(list(map(float,words[2:]))).reshape(n,3)
   return np.vstack((np.column_stack((points,np.zeros(n))),[-1.,1.,float(n),-2.])).reshape(-1)
  p=ROOT.parents[1]/'tools/NativeProjection.py';tree=ast.parse(p.read_text());tree.body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
  self.ns=dict(log=LOG,run_native_command=lambda command,*args:command(*args),os=os,json=json,np=np,cmds=SimpleNamespace(optionVar=option,pluginInfo=lambda *a,**kw:['wctProjectPoints'],wctProjectPoints=command),StrictDriver=SimpleNamespace(binary_identity=identity))
  exec(compile(tree,str(p),'exec'),self.ns);self.points=np.arange(120,dtype=float).reshape(40,3)
 def tearDown(self):self.env.stop()
 def project(self,points=None):
  with contextlib.redirect_stdout(io.StringIO()):return self.ns['compute_if_enabled']('reference',self.points if points is None else points)
 def test_no_audit_means_original_path(self):self.assertIsNone(self.project());self.assertFalse(self.calls)
 def test_validated_mode_preserves_exact_values_and_order(self):
  self.ns['remember_audit'](2);np.testing.assert_array_equal(self.project(),self.points);self.assertEqual(self.ns['_BATCHES'],1)
 def test_small_batches_keep_python_path(self):
  self.ns['remember_audit'](2);self.assertIsNone(self.project(self.points[:12]));self.assertFalse(self.calls)
 def test_audit_exercises_small_batches_too(self):
  os.environ.update(WCT_NATIVE_PROJECT_AUDIT='1',WCT_NATIVE_PROJECT_MODE='2');np.testing.assert_array_equal(self.project(self.points[:12]),self.points[:12])
 def test_changed_binary_invalidates_record(self):
  self.ns['remember_audit'](2);self.sha='binary2';self.assertIsNone(self.project())
 def test_old_plugin_retains_python(self):
  self.ns['remember_audit'](2);self.version='1.8.0';self.assertIsNone(self.project())
 def test_explicit_disable_overrides_validated_mode(self):
  self.ns['remember_audit'](2);os.environ['WCT_NATIVE_PROJECT']='0';self.assertIsNone(self.project())
 def test_nonfinite_or_malformed_response_never_returns_partial_points(self):
  self.ns['remember_audit'](2)
  for values in ([1,2,3],np.full((41,4),float('nan')),np.zeros((41,4)),np.vstack((np.column_stack((self.points,np.full(40,-1))),[-1,1,40,-2]))):
   self.ns['cmds'].wctProjectPoints=lambda *a,data=values:data;self.assertIsNone(self.project())
  self.assertEqual(self.ns['_BATCHES'],0);self.assertEqual(self.ns['_FALLBACKS'],4)
 def test_command_error_keeps_original_fallback(self):
  self.ns['remember_audit'](2)
  def fail(*a):raise RuntimeError('projection failed')
  self.ns['cmds'].wctProjectPoints=fail;self.assertIsNone(self.project());self.assertEqual(self.ns['_FALLBACKS'],1)
 def test_invalid_modes_cannot_be_saved(self):
  for mode in (1,3,-1,True):
   with self.assertRaises(ValueError):self.ns['remember_audit'](mode)
 def test_clear_audit_disables_native(self):
  self.ns['remember_audit'](2);self.ns['clear_audit']();self.assertIsNone(self.project())
 def test_input_serialization_roundtrips_one_ulp(self):
  self.ns['remember_audit'](2);self.points[0,0]=np.nextafter(1.,2.);np.testing.assert_array_equal(self.project(),self.points)

class SurfaceHookTests(unittest.TestCase):
 def setUp(self):
  source=ROOT.parents[1]/'tools/RestPoseSurface.py';cls=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='RestPoseSurface');method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='project_to_surface')
  self.calls=[];self.ns=dict(np=np,log=LOG)
  class Selection:
   def add(self,*a):pass
   def getDagPath(self,i):return 'mesh'
  def closest(point,space):self.calls.append(point);return SimpleNamespace(x=point[0],y=point[1],z=0),0
  self.ns['om']=SimpleNamespace(MSelectionList=Selection,MFnMesh=lambda path:SimpleNamespace(getClosestPoint=closest),MPoint=lambda *p:p,MSpace=SimpleNamespace(kWorld=0))
  exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),self.ns)
  self.surface=SimpleNamespace(triangulate_name='reference',copy_data=lambda arr:arr+100)
  self.points=np.array([[1.,2.,3.],[4.,5.,6.]])
 def test_native_result_bypasses_original_queries(self):
  self.ns['_native_project_points']=lambda *a:self.points+10
  np.testing.assert_array_equal(self.ns['project_to_surface'](self.surface,self.points),self.points+10);self.assertFalse(self.calls)
 def test_none_or_native_exception_uses_original_exact_loop(self):
  def fail(*a):raise RuntimeError('backend unavailable')
  for backend in (lambda *a:None,fail):
   self.ns['_native_project_points']=backend;result=self.ns['project_to_surface'](self.surface,self.points);np.testing.assert_array_equal(result,[[1,2,0],[4,5,0]])
 def test_explicit_approximate_path_unchanged(self):
  self.ns['_native_project_points']=lambda *a:(_ for _ in ()).throw(AssertionError('should not call'))
  np.testing.assert_array_equal(self.ns['project_to_surface'](self.surface,self.points,use_mesh_api=False),self.points+100)
if __name__=='__main__':unittest.main()
