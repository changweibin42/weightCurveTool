# -*- coding: utf-8 -*-
"""V17.9.0: exact per-curve raw side-query reuse and query timings.

Only uncalibrated raw scalar/None values are stored. References, signs, masks,
holes, connected components and weights are never cached here.
"""
import os
from collections import OrderedDict
from time import perf_counter
import numpy as np
import maya.cmds as cmds

from ..Logger import log

from .CacheFingerprint import fingerprint
import maya.api.OpenMaya as om

try:
    from .NativeStrictQueries import compute_if_enabled as _native_strict_backend
except ImportError:
    _native_strict_backend = None

_CACHE=OrderedDict()
_LIMIT=12
_VALUE_LIMIT=100000


def clear_cache():
    _CACHE.clear()


def _curve_key(common,path,curve):
    name=path.fullPathName();matrix=path.inclusiveMatrix()
    def cvs(space):
        return np.asarray([(p.x,p.y,p.z,p.w) for p in curve.cvPositions(space)],dtype=np.float64)
    return (common,fingerprint([name,cmds.ls(name,uuid=True),(curve.degree,curve.form),
        cvs(om.MSpace.kObject),cvs(om.MSpace.kWorld),
        np.asarray(curve.knots(),dtype=np.float64),
        np.asarray([[matrix.getElement(i,j) for j in range(4)] for i in range(4)],dtype=np.float64)]))


class _Values(dict):
    def __init__(self,shared):
        super().__init__(shared);self.inherited=set(shared);self.used=set();self.computed=set()
    def __getitem__(self,key):
        if key in self.inherited:self.used.add(key)
        return super().__getitem__(key)
    def __setitem__(self,key,value):
        self.computed.add(key);return super().__setitem__(key,value)


class _Probe:
    def __init__(self):self.seconds={};self.calls={};self.native_vertices=0;self.native_seconds=0.
    def call(self,name,fn,*args,**kwargs):
        started=perf_counter()
        try:return fn(*args,**kwargs)
        finally:
            self.seconds[name]=self.seconds.get(name,0.)+perf_counter()-started
            self.calls[name]=self.calls.get(name,0)+1


class _Curve:
    def __init__(self,curve,probe):self.curve=curve;self.probe=probe
    def __getattr__(self,name):return getattr(self.curve,name)
    def closestPoint(self,*args,**kw):return self.probe.call('curve',self.curve.closestPoint,*args,**kw)
    def tangent(self,*args,**kw):return self.probe.call('tangent',self.curve.tangent,*args,**kw)


class _Mesh:
    def __init__(self,mesh,probe):self.mesh=mesh;self.probe=probe
    def __getattr__(self,name):return getattr(self.mesh,name)
    def getClosestPoint(self,*args,**kw):return self.probe.call('mesh',self.mesh.getClosestPoint,*args,**kw)
    def getClosestNormal(self,*args,**kw):return self.probe.call('normal',self.mesh.getClosestNormal,*args,**kw)


class QuerySession:
    def __init__(self,mesh,common,graph=None,mesh_name=None,tolerance=None,rings=None):
        self.mesh=mesh;self.common=common;self.rows=[]
        self.graph=graph;self.mesh_name=mesh_name;self.tolerance=tolerance;self.rings=rings
    def boundary(self,label,path,curve,vertices=()):
        key=None
        if self.common is not None:
            try:key=_curve_key(self.common,path,curve)
            except Exception as exc:log.info('[WCT BoundaryQueryCache V17.9.0] curve uncached: '+str(exc))
        shared=_CACHE.get(key,{}) if key is not None else {}
        values=_Values(shared);probe=_Probe()
        self.rows.append((label,key,values,probe))
        backend=globals().get('_native_strict_backend')
        missing=sorted(v for v in vertices if v not in values)
        if backend and missing and self.mesh_name is not None:
            started=perf_counter()
            result=backend(self.mesh_name,path.fullPathName(),missing,self.tolerance,self.rings,self.graph)
            if result is not None:
                # The driver validates the entire batch before any commit.
                for vertex,value in result.items():values[vertex]=value
                probe.native_vertices=len(result);probe.native_seconds=perf_counter()-started
        return _Curve(curve,probe),_Mesh(self.mesh,probe),values
    def finish(self):
        # Commit only after the complete current region has been classified.
        for label,key,values,probe in self.rows:
            reused,computed=len(values.used),len(values.computed)
            if key is not None:
                _CACHE[key]=dict(values);_CACHE.move_to_end(key)
            log.info('[WCT BoundaryQueries V17.9.0] {} reused={} computed={} curve={:.4f}s/{} mesh={:.4f}s/{} normal={:.4f}s/{} tangent={:.4f}s/{} native={}/{:.4f}s'.format(
                label,reused,computed,
                probe.seconds.get('curve',0.),probe.calls.get('curve',0),
                probe.seconds.get('mesh',0.),probe.calls.get('mesh',0),
                probe.seconds.get('normal',0.),probe.calls.get('normal',0),
                probe.seconds.get('tangent',0.),probe.calls.get('tangent',0),probe.native_vertices,probe.native_seconds))
        while len(_CACHE)>_LIMIT or sum(map(len,_CACHE.values()))>_VALUE_LIMIT:
            _CACHE.popitem(last=False)


def prepare_queries(mesh,points,graph,tolerance,rings,mesh_name=None):
    started=perf_counter();common=None
    geometry_key=getattr(graph,'entry',{}).get('geometry_key')
    enabled=os.environ.get('WCT_BOUNDARY_QUERY_CACHE','1')!='0'
    if enabled and geometry_key is not None:
        try:
            normals=mesh.getNormals(om.MSpace.kWorld);object_normals=mesh.getNormals(om.MSpace.kObject)
            counts,ids=mesh.getNormalIds()
            # Mesh graph signature includes complete geometry/edge order.
            # Add all normals and corner mappings; normal edits must invalidate.
            common=fingerprint([1,geometry_key.hex(),float(tolerance),int(rings),
                np.asarray([(p.x,p.y,p.z,p.w) for p in points],dtype=np.float64),
                np.asarray([(n.x,n.y,n.z) for n in normals],dtype=np.float64),
                np.asarray([(n.x,n.y,n.z) for n in object_normals],dtype=np.float64),
                np.asarray(counts,dtype=np.int64),np.asarray(ids,dtype=np.int64)])
        except Exception as exc:log.info('[WCT BoundaryQueryCache V17.9.0] mesh uncached: '+str(exc))
    log.info('[WCT BoundaryQueryCache V17.9.0] {} prepare={:.4f}s'.format(
        'enabled' if common is not None else 'timing-only',perf_counter()-started))
    return QuerySession(mesh,common,graph,mesh_name,tolerance,rings)
