# -*- coding: utf-8 -*-
"""V17.8.16: exact mesh graph and topology-ring reuse, never region reuse."""
import os
from collections import OrderedDict
from time import perf_counter
from types import MappingProxyType
import numpy as np
import maya.cmds as cmds

from ..Logger import log

from .CacheFingerprint import fingerprint
import maya.api.OpenMaya as om

_CACHE=OrderedDict()
_LIMIT=3


def clear_cache():
    _CACHE.clear()


def _snapshot(path,mesh,points):
    name=path.fullPathName();matrix=path.inclusiveMatrix()
    counts,vertices=mesh.getVertices();tc,tv=mesh.getTriangles()
    counts=np.asarray(counts,dtype=np.int64);vertices=np.asarray(vertices,dtype=np.int64)
    # Include edge IDs/order: shortest-path ties depend on insertion order.
    edges=np.asarray([mesh.getEdgeVertices(i) for i in range(int(mesh.numEdges))],dtype=np.int64)
    key=fingerprint([name,cmds.ls(name,uuid=True),
        np.asarray([(p.x,p.y,p.z,p.w) for p in points],dtype=np.float64),
        np.asarray([(p.x,p.y,p.z,p.w) for p in mesh.getPoints(om.MSpace.kObject)],dtype=np.float64),
        np.asarray([[matrix.getElement(i,j) for j in range(4)] for i in range(4)],dtype=np.float64),
        counts,vertices,edges,np.asarray(tc,dtype=np.int64),np.asarray(tv,dtype=np.int64)])
    polygons=[];offset=0
    for count in counts:
        end=offset+int(count);polygons.append(tuple(map(int,vertices[offset:end])));offset=end
    if offset!=len(vertices):raise RuntimeError('Invalid mesh polygon layout')
    return key,tuple(polygons)


class _MeshView:
    """Only polygon lookup is cached; all geometric/normal queries stay live."""
    def __init__(self,mesh,polygons):self.mesh=mesh;self.polygons=polygons
    def __getattr__(self,name):return getattr(self.mesh,name)
    def getPolygonVertices(self,face):return list(self.polygons[int(face)])


class _RegionGraph(tuple):
    def __new__(cls,rows,entry):
        return super().__new__(cls,(MappingProxyType(dict(row)) for row in rows))
    def __init__(self,rows,entry):
        self.entry=entry;self.expansion_hits=0;self.expansion_misses=0
    def expand_vertices(self,vertices,rings):
        # Single-vertex neighborhoods are the repeated hot path. Arbitrary
        # curve-loop seed sets still use the original expansion each time.
        seeds=tuple(vertices)
        key=(seeds,int(rings))
        if len(seeds)!=1:return self._expand(key)
        if os.environ.get('WCT_TOPOLOGY_RING_CACHE','1')=='0':
            return self._expand(key)
        cache=self.entry['rings'];saved=cache.get(key)
        if saved is not None:
            self.expansion_hits+=1;cache.move_to_end(key);return saved.copy()
        self.expansion_misses+=1;result=self._expand(key)
        saved=result.copy();cache[key]=saved
        self.entry['ring_items']+=len(key[0])+len(saved)
        while len(cache)>2048 or self.entry['ring_items']>250000:
            old,value=cache.popitem(last=False)
            self.entry['ring_items']-=len(old[0])+len(value)
        return result
    def _expand(self,key):
        seeds,rings=key;result=set(seeds);front=set(seeds)
        for _ in range(rings):
            front={nb for v in front for nb in self[v]}-result
            result.update(front)
        return result


def prepare_region_mesh(path,mesh,points,compute):
    """compute preserves the original Maya edge iterator and length math."""
    if os.environ.get('WCT_REGION_MESH_CACHE','1')=='0':
        graph,median=compute();return mesh,graph,median
    started=perf_counter()
    try:key,polygons=_snapshot(path,mesh,points)
    except Exception as exc:
        log.info('[WCT MeshCache V17.8.16] uncached: '+str(exc))
        graph,median=compute();return mesh,graph,median
    entry=_CACHE.get(key);reused=entry is not None
    if entry is None:
        graph,median=compute()
        entry=dict(geometry_key=key,rows=tuple(tuple(row.items()) for row in graph),median=median,
                   polygons=polygons,rings=OrderedDict(),ring_items=0)
        _CACHE[key]=entry
    _CACHE.move_to_end(key)
    while len(_CACHE)>_LIMIT:_CACHE.popitem(last=False)
    graph=_RegionGraph(entry['rows'],entry)
    log.info('[WCT MeshCache V17.8.16] {} prepare={:.4f}s'.format('hit' if reused else 'miss',perf_counter()-started))
    return _MeshView(mesh,entry['polygons']),graph,entry['median']
