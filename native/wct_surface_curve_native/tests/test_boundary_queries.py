import ast,os,json,hashlib,contextlib,io,importlib.util
from pathlib import Path
from collections import OrderedDict
from types import SimpleNamespace
from time import perf_counter
from unittest.mock import patch
import unittest
import numpy as np
from test_mesh_cache import Point,load as load_mesh
ROOT=Path(__file__).resolve().parents[1]
class Dag:
 def __init__(self,name='curve'):self.name=name;self.matrix=np.eye(4)
 def fullPathName(self):return self.name
 def inclusiveMatrix(self):return SimpleNamespace(getElement=lambda i,j:self.matrix[i,j])
def load():
 path=ROOT/'weightCurveTool/tools/BoundaryQueryCache.py';tree=ast.parse(path.read_text());tree.body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
 ns=dict(__name__="unit_no_package",__package__="",os=os,json=json,hashlib=hashlib,OrderedDict=OrderedDict,perf_counter=perf_counter,np=np,cmds=SimpleNamespace(ls=lambda name,**kw:[name+'uuid']),om=SimpleNamespace(MSpace=SimpleNamespace(kWorld=0,kObject=1)))
 exec(compile(tree,str(path),'exec'),ns);return ns
class QueryCacheTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.ns=load();self.path=Dag();self.points=[Point(0),Point(1),Point(0,1)];self.normal=Point(0,0,1);self.ids=[0,0,0];self.graph=SimpleNamespace(entry={'geometry_key':b'mesh'})
  self.mesh=SimpleNamespace(getNormals=lambda space:[self.normal],getNormalIds=lambda:([3],self.ids))
  self.cvs=[Point(0),Point(1)];self.knots=[0.,1.]
  self.curve=SimpleNamespace(degree=1,form=3,cvPositions=lambda space:self.cvs,knots=lambda:self.knots)
 def tearDown(self):self.env.stop()
 def session(self,tol=1e-7,rings=4):
  with contextlib.redirect_stdout(io.StringIO()):return self.ns['prepare_queries'](self.mesh,self.points,self.graph,tol,rings)
 def boundary(self,session=None):
  s=session or self.session();_,_,values=s.boundary('FWD',self.path,self.curve);return s,values
 def finish(self,s):
  with contextlib.redirect_stdout(io.StringIO()):s.finish()
 def seed(self):
  s,values=self.boundary();values[0]=.25;values[1]=None;self.finish(s)
 def test_exact_repeat_reuses_scalar_and_none(self):
  self.seed();s,v=self.boundary();self.assertEqual(v[0],.25);self.assertIsNone(v[1]);self.assertEqual(v.used,{0,1});self.assertFalse(v.computed)
 def test_one_ulp_cv_change_invalidates(self):
  self.seed();self.cvs[1].x=np.nextafter(1.,2.);self.assertFalse(self.boundary()[1])
 def test_normal_edit_invalidates_without_mesh_movement(self):
  self.seed();self.normal.z=.99;self.assertFalse(self.boundary()[1])
 def test_corner_normal_index_edit_invalidates(self):
  self.seed();self.ids[0]=1;self.assertFalse(self.boundary()[1])
 def test_geometry_key_edit_invalidates(self):
  self.seed();self.graph.entry['geometry_key']=b'other';self.assertFalse(self.boundary()[1])
 def test_point_edit_invalidates_even_with_bad_external_graph_key(self):
  self.seed();self.points[0].x=1e-14;self.assertFalse(self.boundary()[1])
 def test_matrix_and_knots_edit_invalidate(self):
  self.seed();self.path.matrix[0,3]=1e-14;self.assertFalse(self.boundary()[1]);self.seed();self.knots[1]=2.;self.assertFalse(self.boundary()[1])
 def test_tolerance_and_ring_changes_invalidate(self):
  self.seed();self.assertFalse(self.boundary(self.session(tol=2e-7))[1]);self.assertFalse(self.boundary(self.session(rings=5))[1])
 def test_replaced_curve_uuid_invalidates(self):
  self.seed();self.ns['cmds'].ls=lambda *a,**kw:['replaced'];self.assertFalse(self.boundary()[1])
 def test_unfinished_session_cannot_poison_shared_cache(self):
  self.seed();s,v=self.boundary();v[0]=99.;self.assertEqual(self.boundary()[1][0],.25)
 def test_commit_is_copy_isolated(self):
  s,v=self.boundary();v[0]=.1;self.finish(s);v[0]=99.;self.assertEqual(self.boundary()[1][0],.1)
 def test_no_geometry_key_is_timing_only(self):
  self.graph.entry={};self.seed();self.assertFalse(self.ns['_CACHE'])
 def test_disable_cache_keeps_timing_and_no_reuse(self):
  self.seed();os.environ['WCT_BOUNDARY_QUERY_CACHE']='0';s,v=self.boundary();self.assertFalse(v);self.assertIsNone(s.common)
 def test_unsupported_normals_falls_back_without_breaking_query(self):
  self.mesh.getNormals=lambda *a:(_ for _ in ()).throw(RuntimeError('unsupported'))
  s,v=self.boundary();self.assertIsNone(s.common);v[0]=1.;self.finish(s);self.assertFalse(self.ns['_CACHE'])
 def test_entry_and_value_limits(self):
  for i in range(20):self.path.name='curve'+str(i);self.seed()
  self.assertLessEqual(len(self.ns['_CACHE']),12);self.ns['_VALUE_LIMIT']=1;s,v=self.boundary();v.update({2:2,3:3});self.finish(s);self.assertLessEqual(sum(map(len,self.ns['_CACHE'].values())),1)
 def test_native_prefetch_only_requests_uncached_vertices(self):
  self.seed();calls=[]
  self.ns['_native_strict_backend']=lambda *args:calls.append(args) or {2:.75,3:None}
  session=self.ns['QuerySession'](self.mesh,self.session().common,self.graph,'mesh',1e-7,4)
  _,_,values=session.boundary('FWD',self.path,self.curve,vertices={0,1,2,3})
  self.assertEqual(calls[0][2],[2,3]);self.assertEqual(values[0],.25);self.assertEqual(values[2],.75);self.assertIsNone(values[3])
  self.assertEqual(session.rows[0][3].native_vertices,2)
 def test_native_failure_leaves_python_query_cache_empty(self):
  self.ns['_native_strict_backend']=lambda *args:None
  session=self.ns['QuerySession'](self.mesh,None,self.graph,'mesh',1e-7,4)
  _,_,values=session.boundary('FWD',self.path,self.curve,vertices={0,1})
  self.assertFalse(values);self.assertEqual(session.rows[0][3].native_vertices,0)
 def test_proxies_preserve_arguments_and_results(self):
  probe=self.ns['_Probe']();seen=[];sentinel=object()
  def fn(*args,**kw):seen.append((args,kw));return sentinel
  curve=self.ns['_Curve'](SimpleNamespace(closestPoint=fn,tangent=fn),probe)
  mesh=self.ns['_Mesh'](SimpleNamespace(getClosestPoint=fn,getClosestNormal=fn),probe)
  for query in (curve.closestPoint,curve.tangent,mesh.getClosestPoint,mesh.getClosestNormal):self.assertIs(query('point',space=3),sentinel)
  self.assertEqual(seen,[(('point',),{'space':3})]*4);self.assertEqual(probe.calls,dict(curve=1,tangent=1,mesh=1,normal=1))

class IntegratedBoundaryTests(unittest.TestCase):
 def test_warm_moved_boundary_and_normal_edit_match_fresh_geometry(self):
  path=ROOT/'verification/curves/tests/test_strict_integration.py';spec=importlib.util.spec_from_file_location('boundary_integration',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  scene=m.Scene();original_mesh=scene.om.MFnMesh
  class Mesh:
   def __init__(self):self.base=original_mesh(None)
   def __getattr__(self,name):return getattr(self.base,name)
   numEdges=len(scene.edges)
   def getVertices(self):return [len(f) for f in scene.faces],[v for f in scene.faces for v in f]
   def getTriangles(self):return [2]*len(scene.faces),[v for a,b,c,d in scene.faces for v in (a,b,c,a,c,d)]
   def getEdgeVertices(self,i):return scene.edges[i]
   def getNormals(self,space):return [m.V(0,0,scene.normal)]
   def getNormalIds(self):return [len(f) for f in scene.faces],[0 for f in scene.faces for _ in f]
  scene.om.MFnMesh=lambda path:Mesh();scene.om.MSpace.kObject=1
  factory=scene.om.MFnNurbsCurve;radius=[10.]
  def curve(path):
   name=path.name if isinstance(path,Dag) else path;result=factory(name)
   if name=='fwd':result.r=radius[0]
   return result
  scene.om.MFnNurbsCurve=type('Factory',(),{'kOpen':1,'__new__':lambda cls,path:curve(path)})
  ns=m.region_ns(scene);ns['_wct_shape']=lambda name,kind:Dag(name)
  topology=load_mesh();queries=load();sessions=[]
  ns['_prepare_region_mesh']=topology['prepare_region_mesh']
  def prepare(*args,**kwargs):
   s=queries['prepare_queries'](*args,**kwargs);sessions.append(s);return s
  ns['_prepare_boundary_queries']=prepare
  def region():
   r=ns['build_strict_region']('mesh',('ridge','fwd','bwd'));q=ns['weight_coordinates'](r,'mesh');return r,q
  def compare(a,b):
   np.testing.assert_array_equal(a[0]['mask'],b[0]['mask']);np.testing.assert_array_equal(a[0]['fwd_outer'],b[0]['fwd_outer']);np.testing.assert_array_equal(a[0]['decay_field'],b[0]['decay_field'])
   for x,y in zip(a[1],b[1]):np.testing.assert_array_equal(x,y)
  with contextlib.redirect_stdout(io.StringIO()):
   cold=region();warm=region();compare(cold,warm)
   self.assertTrue(all(len(row[2].used)>0 and not row[2].computed for row in sessions[-1].rows))
   radius[0]=11.;changed=region()
   stats={label:(len(values.used),len(values.computed)) for label,key,values,probe in sessions[-1].rows}
   self.assertEqual(stats['FWD'][0],0);self.assertGreater(stats['FWD'][1],0);self.assertGreater(stats['BWD'][0],0)
   del ns['_prepare_boundary_queries'];fresh=region();compare(changed,fresh)
   ns['_prepare_boundary_queries']=prepare;scene.normal=-1;flipped=region()
   self.assertTrue(all(not row[2].used and row[2].computed for row in sessions[-1].rows))
   del ns['_prepare_boundary_queries'];fresh_flip=region();compare(flipped,fresh_flip)
  self.assertFalse(np.array_equal(cold[0]['mask'],changed[0]['mask']))

class FoldedBoundaryCacheTests(unittest.TestCase):
 def test_folded_strip_cached_side_classification_matches_uncached(self):
  import sys,importlib
  folder=str(ROOT/'verification/curves/tests');sys.path.insert(0,folder)
  try:fold=importlib.import_module('test_folded_decay')
  finally:sys.path.remove(folder)
  scene=fold.FoldedScene();region=scene.region();ns=fold.region_ns(scene);cache=load()
  cache['_curve_key']=lambda common,path,curve:(common,path,curve.s)
  mesh=scene.mesh();arguments=(mesh,region['points'],region['graph'],region['coarse_interior'],region['fwd_loop'],region['bwd_loop'],region['median'],region['curves'])
  with contextlib.redirect_stdout(io.StringIO()):
   expected=ns['_wct_strict_refine'](*arguments)
   cold=cache['QuerySession'](mesh,b'folded');actual=ns['_wct_strict_refine'](*arguments,query_session=cold);cold.finish()
   warm=cache['QuerySession'](mesh,b'folded');again=ns['_wct_strict_refine'](*arguments,query_session=warm);warm.finish()
  for result in (actual,again):
   for i in (0,1,2,3,5,6,7):self.assertEqual(expected[i],result[i])
  self.assertTrue(all(not probe.calls for label,key,values,probe in warm.rows))
  self.assertTrue(all(values.used for label,key,values,probe in warm.rows))

if __name__=='__main__':unittest.main()
