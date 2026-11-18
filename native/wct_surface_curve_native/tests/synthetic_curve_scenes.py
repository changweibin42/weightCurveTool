"""Analytic Maya API doubles for strict-region cache tests."""
import ast
from collections import Counter, deque
import heapq
import math
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace

import numpy as np


TOOLS = Path(__file__).resolve().parents[3] / 'tools'
LOG = run_path(str(TOOLS.parent / 'Logger.py'))['log']


class V:
    def __init__(self,x,y,z=0.,w=1.): self.x,self.y,self.z,self.w=x,y,z,w
    def __add__(self,o):return V(self.x+o.x,self.y+o.y,self.z+o.z)
    def __sub__(self,o):return V(self.x-o.x,self.y-o.y,self.z-o.z)
    def __mul__(self,o):return self.x*o.x+self.y*o.y+self.z*o.z if isinstance(o,V) else V(self.x*o,self.y*o,self.z*o)
    def __xor__(self,o):return V(self.y*o.z-self.z*o.y,self.z*o.x-self.x*o.z,self.x*o.y-self.y*o.x)
    def length(self):return math.sqrt(self*self)


class Scene:
    def __init__(self,orientation=1,normal=1):
        self.points=[V(x,y) for y in range(-14,15) for x in range(-14,15)]
        self.ids={(p.x,p.y):i for i,p in enumerate(self.points)}
        self.graph=[{} for _ in self.points]
        self.orientation,self.normal=orientation,normal
        self.faces=[]
        for y in range(-14,14):
            for x in range(-14,14):
                self.faces.append([self.ids[(x,y)],self.ids[(x+1,y)],self.ids[(x+1,y+1)],self.ids[(x,y+1)]])
        self.edges=[]
        for i,p in enumerate(self.points):
            for xy in ((p.x+1,p.y),(p.x,p.y+1)):
                if xy in self.ids:
                    j=self.ids[xy];self.edges.append((i,j));self.graph[i][j]=self.graph[j][i]=1.
        self.om=SimpleNamespace(MPoint=V,MSpace=SimpleNamespace(kWorld=0),MFnMesh=lambda path:self.mesh(),
            MFnNurbsCurve=lambda name:self.curve(name),MItMeshEdge=lambda path:self.iterator())
        self.om.MFnNurbsCurve=type('CurveFactory',(),{'kOpen':1,'__new__':lambda cls,name:self.curve(name)})
    def mesh(self):
        scene=self
        class Mesh:
            numVertices=len(scene.points)
            def getPoints(self,space):return scene.points
            def getVertices(self):return [4]*len(scene.faces),[i for f in scene.faces for i in f]
            def getClosestPoint(self,p,space):
                x=min(13,max(-14,int(math.floor(p.x))));y=min(13,max(-14,int(math.floor(p.y))))
                fid=(y+14)*28+(x+14)
                return V(p.x,p.y),fid
            def getPolygonVertices(self,fid):return scene.faces[fid]
            def getClosestNormal(self,p,space):return V(0,0,scene.normal),0
        return Mesh()
    def curve(self,name):
        scene=self
        class Curve:
            form=3;degree=3
            def __init__(self):self.r={'ridge':8.,'fwd':10.,'bwd':6.}[name]
            def length(self):return 2*math.pi*self.r
            def findParamFromLength(self,length):return scene.orientation*length/self.r
            def getPointAtParam(self,t,space):return V(self.r*math.cos(t),self.r*math.sin(t),.2)
            def closestPoint(self,p,tolerance=1e-3,space=0):
                t=math.atan2(p.y,p.x)
                return self.getPointAtParam(t,0),t
            def tangent(self,t,space):return V(-math.sin(t)*scene.orientation,math.cos(t)*scene.orientation,0)
            def cvPositions(self,space):return [self.getPointAtParam(i*math.pi/4,space) for i in range(8)]
            def knots(self):return list(range(10))
        return Curve()
    def iterator(self):
        scene=self
        class Edges:
            def __init__(self):self.i=0
            def isDone(self):return self.i==len(scene.edges)
            def vertexId(self,k):return scene.edges[self.i][k]
            def next(self):self.i+=1
            def index(self):return self.i
        return Edges()


def region_ns(scene):
    path = TOOLS / 'StrictCurveRegion.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name != '_wct_shape'
    ]
    namespace = dict(
        log=LOG, np=np, math=math, heapq=heapq, deque=deque, Counter=Counter,
        om=scene.om, _wct_shape=lambda name, kind: name,
        STRICT_EPS_FACTOR=1e-7, REFINE_RINGS=3, SAMPLE_SPACING=.2,
    )
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace


class FoldedScene:
    """Open toroidal strip: width bends through >180 degrees, never wraps.

    All three curves are closed angular rings. Mesh topology gives a known
    order BWD(s=-.4), ridge(s=0), FWD(s=4). A ridge-plane test reverses the
    FWD label for much of this strip, despite correct vertex membership.
    """
    def __init__(self):
        self.n=24;self.m=101
        self.s=np.linspace(-.5,4.5,self.m)
        self.points=[self.point(s,i*2*math.pi/self.n) for s in self.s for i in range(self.n)]
        self.graph=[{} for _ in self.points]
        self.faces=[]
        for j in range(self.m):
            for i in range(self.n):
                a=j*self.n+i
                for b in (j*self.n+(i+1)%self.n,(j+1)*self.n+i if j+1<self.m else a):
                    if a!=b:self.graph[a][b]=self.graph[b][a]=(self.points[a]-self.points[b]).length()
                if j+1<self.m:
                    self.faces.append([a,j*self.n+(i+1)%self.n,(j+1)*self.n+(i+1)%self.n,(j+1)*self.n+i])
        self.om=SimpleNamespace(MPoint=V,MSpace=SimpleNamespace(kWorld=0),MFnMesh=lambda path:self.mesh(),MFnNurbsCurve=lambda name:self.curve(name))
    def point(self,s,t):return V((3+math.sin(s))*math.cos(t),(3+math.sin(s))*math.sin(t),1-math.cos(s))
    def mesh(self):
        scene=self
        class Mesh:
            def getClosestPoint(self,p,space):
                s=math.atan2(math.hypot(p.x,p.y)-3,1-p.z)
                if s<-.500001:s+=2*math.pi
                j=max(0,min(scene.m-2,int(round((s+.5)/.05))))
                i=int((math.atan2(p.y,p.x)%(2*math.pi))*scene.n/(2*math.pi))%scene.n
                return p,j*scene.n+i
            def getPolygonVertices(self,fid):return scene.faces[fid]
            def getClosestNormal(self,p,space):
                s=math.atan2(math.hypot(p.x,p.y)-3,1-p.z);t=math.atan2(p.y,p.x)
                return V(math.sin(s)*math.cos(t),math.sin(s)*math.sin(t),-math.cos(s)),0
        return Mesh()
    def curve(self,name):
        scene=self
        class Curve:
            def __init__(self):self.s={'ridge':0.,'fwd':4.,'bwd':-.4}[name]
            def closestPoint(self,p,tolerance=1e-3,space=0):
                t=math.atan2(p.y,p.x);return scene.point(self.s,t),t
            def tangent(self,t,space):return V(-math.sin(t),math.cos(t),0)
        return Curve()
    def region(self):
        n=self.n;mask=np.zeros(len(self.points),bool);mask[3*n:90*n]=True
        loops={name:set(range(j*n,(j+1)*n)) for name,j in [('ridge',10),('fwd',90),('bwd',2)]}
        sources={}
        for name,s in [('ridge',0.),('fwd',4.),('bwd',-.4)]:
            sources[name]=[]
            for i in range(n):
                t=i*2*math.pi/n;p=self.point(s,t);_,face=self.mesh().getClosestPoint(p,0)
                sources[name].append((p,face,t))
        return dict(mask=mask,points=self.points,graph=self.graph,median=.05,
            curves=('ridge','fwd','bwd'),ridge_anchors=sorted(loops['ridge']),
            coarse_interior=set(np.flatnonzero(mask)),fwd_loop=loops['fwd'],bwd_loop=loops['bwd'],curve_samples=sources)
