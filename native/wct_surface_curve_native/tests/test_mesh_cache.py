import ast,os,hashlib,json,contextlib,io,math,importlib.util
from time import perf_counter
from collections import OrderedDict
from types import MappingProxyType,SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
class Point:
 def __init__(self,x,y=0,z=0,w=1):self.x,self.y,self.z,self.w=x,y,z,w
class Dag:
 def __init__(self):self.matrix=np.eye(4)
 def fullPathName(self):return 'mesh'
 def inclusiveMatrix(self):return SimpleNamespace(getElement=lambda i,j:self.matrix[i,j])
def load():
 path=ROOT/'weightCurveTool/tools/MeshRegionCache.py';tree=ast.parse(path.read_text());tree.body=[n for n in tree.body if not isinstance(n,(ast.Import,ast.ImportFrom))]
 ns=dict(os=os,hashlib=hashlib,json=json,OrderedDict=OrderedDict,MappingProxyType=MappingProxyType,np=np,perf_counter=perf_counter,cmds=SimpleNamespace(ls=lambda *a,**kw:['uuid']),om=SimpleNamespace(MSpace=SimpleNamespace(kObject=0)))
 exec(compile(tree,str(path),'exec'),ns);return ns
class MeshCacheTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{},clear=True);self.env.start();self.ns=load();self.path=Dag();self.points=[Point(0),Point(1),Point(0,1)];self.edges=[(0,1),(1,2),(2,0)];self.polygons=[0,1,2];self.triangles=[0,1,2];self.calls=0;self.normal='old'
  self.mesh=SimpleNamespace(numEdges=3,getVertices=lambda:([3],self.polygons),getTriangles=lambda:([1],self.triangles),getPoints=lambda space:self.points,getEdgeVertices=lambda i:self.edges[i],getPolygonVertices=lambda i:self.polygons,getClosestNormal=lambda *a:self.normal)
 def tearDown(self):self.env.stop()
 def compute(self):self.calls+=1;return [{1:1.,2:1.},{0:1.,2:math.sqrt(2.)},{1:math.sqrt(2.),0:1.}],1.
 def prepare(self):
  with contextlib.redirect_stdout(io.StringIO()):return self.ns['prepare_region_mesh'](self.path,self.mesh,self.points,self.compute)
 def test_warm_reuses_graph_and_preserves_neighbor_order(self):
  first=self.prepare();second=self.prepare();self.assertEqual(self.calls,1)
  self.assertEqual([list(row.items()) for row in first[1]],[list(row.items()) for row in second[1]])
 def test_one_ulp_world_coordinate_invalidates(self):
  self.prepare();self.points[1].x=np.nextafter(1.,2.);self.prepare();self.assertEqual(self.calls,2)
 def test_face_order_change_invalidates(self):
  self.prepare();self.polygons.reverse();self.prepare();self.assertEqual(self.calls,2)
 def test_edge_id_order_change_invalidates(self):
  self.prepare();self.edges.reverse();self.prepare();self.assertEqual(self.calls,2)
 def test_triangle_change_invalidates(self):
  self.prepare();self.triangles.reverse();self.prepare();self.assertEqual(self.calls,2)
 def test_matrix_change_invalidates(self):
  self.prepare();self.path.matrix[0,3]=1e-12;self.prepare();self.assertEqual(self.calls,2)
 def test_uuid_change_invalidates(self):
  self.prepare();self.ns['cmds'].ls=lambda *a,**kw:['replacement'];self.prepare();self.assertEqual(self.calls,2)
 def test_graph_and_neighbors_are_immutable(self):
  _,graph,_=self.prepare()
  with self.assertRaises(TypeError):graph[0][1]=99
  with self.assertRaises(TypeError):graph[0]={}
 def test_cached_polygon_returns_copy(self):
  mesh,_,_=self.prepare();face=mesh.getPolygonVertices(0);face.append(99);self.assertEqual(mesh.getPolygonVertices(0),[0,1,2])
 def test_normal_queries_remain_live(self):
  self.prepare();self.normal='new';mesh,_,_=self.prepare();self.assertEqual(self.calls,1);self.assertEqual(mesh.getClosestNormal(None),'new')
 def test_ring_cache_shared_but_results_isolated(self):
  _,graph,_=self.prepare();first=graph.expand_vertices((0,),1);self.assertEqual(first,{0,1,2});first.clear()
  _,next_graph,_=self.prepare();self.assertEqual(next_graph.expand_vertices((0,),1),{0,1,2});self.assertEqual(next_graph.expansion_hits,1)
 def test_multi_seed_expansion_matches_original_without_caching(self):
  _,graph,_=self.prepare();self.assertEqual(graph.expand_vertices({0,2},1),{0,1,2});self.assertEqual(graph.expansion_hits,0);self.assertEqual(graph.expansion_misses,0)
 def test_failed_compute_never_commits(self):
  def fail():raise RuntimeError('bad mesh')
  self.compute=fail
  with self.assertRaises(RuntimeError):self.prepare()
  self.assertFalse(self.ns['_CACHE'])
 def test_unsupported_snapshot_falls_back_to_original(self):
  self.ns['_snapshot']=lambda *a:(_ for _ in ()).throw(RuntimeError('unsupported'));self.prepare();self.prepare();self.assertEqual(self.calls,2)
 def test_disabling_graph_cache_uses_original_every_time(self):
  os.environ['WCT_REGION_MESH_CACHE']='0';self.prepare();self.prepare();self.assertEqual(self.calls,2)
 def test_disabling_rings_does_not_reuse_neighborhoods(self):
  os.environ['WCT_TOPOLOGY_RING_CACHE']='0';_,graph,_=self.prepare();graph.expand_vertices((0,),2);graph.expand_vertices((0,),2);self.assertFalse(graph.entry['rings'])
 def test_snapshot_cache_is_bounded_and_clearable(self):
  for i in range(8):self.points[1].x=i+1.;self.prepare()
  self.assertEqual(len(self.ns['_CACHE']),3);self.ns['clear_cache']();self.assertFalse(self.ns['_CACHE'])

class RegionIntegrationTests(unittest.TestCase):
 def test_cold_warm_and_changed_fwd_match_original_masks_and_fields(self):
  path=ROOT/'verification/curves/tests/test_strict_integration.py';spec=importlib.util.spec_from_file_location('mesh_integration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  scene=module.Scene();original_factory=scene.om.MFnMesh
  class MeshProxy:
   def __init__(self,base):self.base=base
   def __getattr__(self,name):return getattr(self.base,name)
   numEdges=len(scene.edges)
   def getVertices(self):return [len(f) for f in scene.faces],[v for face in scene.faces for v in face]
   def getTriangles(self):return [2]*len(scene.faces),[v for a,b,c,d in scene.faces for v in (a,b,c,a,c,d)]
   def getEdgeVertices(self,i):return scene.edges[i]
  scene.om.MFnMesh=lambda p:MeshProxy(original_factory(p));scene.om.MSpace.kObject=0
  ns=module.region_ns(scene);ns['_wct_shape']=lambda name,kind:Dag() if kind=='mesh' else name
  cache=load();calls=[]
  def prepare(path,mesh,points,compute):
   def tracked():calls.append(1);return compute()
   return cache['prepare_region_mesh'](path,mesh,points,tracked)
  with contextlib.redirect_stdout(io.StringIO()):
   reference=ns['build_strict_region']('mesh',('ridge','fwd','bwd'));expected=ns['weight_coordinates'](reference,'mesh')
   ns['_prepare_region_mesh']=prepare
   cold=ns['build_strict_region']('mesh',('ridge','fwd','bwd'));qc=ns['weight_coordinates'](cold,'mesh')
   warm=ns['build_strict_region']('mesh',('ridge','fwd','bwd'));qw=ns['weight_coordinates'](warm,'mesh')
  self.assertEqual(len(calls),1);self.assertGreater(warm['graph'].expansion_hits,0)
  for region,q in ((cold,qc),(warm,qw)):
   np.testing.assert_array_equal(reference['mask'],region['mask']);np.testing.assert_array_equal(reference['fwd_outer'],region['fwd_outer']);np.testing.assert_array_equal(reference['decay_field'],region['decay_field'])
   for a,b in zip(expected,q):np.testing.assert_array_equal(a,b)
  factory=scene.om.MFnNurbsCurve
  def new_curve(name):
   result=factory(name)
   if name=='fwd':result.r=11.
   return result
  scene.om.MFnNurbsCurve=type('Factory',(),{'kOpen':1,'__new__':lambda cls,name:new_curve(name)})
  with contextlib.redirect_stdout(io.StringIO()):
   changed=ns['build_strict_region']('mesh',('ridge','fwd','bwd'));actual=ns['weight_coordinates'](changed,'mesh')
   del ns['_prepare_region_mesh']
   fresh=ns['build_strict_region']('mesh',('ridge','fwd','bwd'));expected=ns['weight_coordinates'](fresh,'mesh')
  self.assertEqual(len(calls),1);self.assertFalse(np.array_equal(warm['mask'],changed['mask']))
  np.testing.assert_array_equal(changed['mask'],fresh['mask']);np.testing.assert_array_equal(changed['fwd_outer'],fresh['fwd_outer'])
  for a,b in zip(actual,expected):np.testing.assert_array_equal(a,b)
if __name__=='__main__':unittest.main()
