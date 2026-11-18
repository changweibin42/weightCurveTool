# -*- coding: utf-8 -*-
"""V17.8.14: memoize surface sampling under exact complete geometry inputs.

No masks, side decisions, edge cuts or skin weights are stored. Repeated
Maya arc-length inversion can drift even while CVs remain byte-identical.
Pin that derived sample set until any authoritative input changes.
"""
import os
from collections import OrderedDict
import numpy as np
import maya.cmds as cmds

from ..Logger import log

from .CacheFingerprint import fingerprint
import maya.api.OpenMaya as om

_CACHE = OrderedDict()
_LIMIT = 24


def clear_cache():
    _CACHE.clear()


def _points(values):
    return np.asarray([(p.x,p.y,p.z,p.w) for p in values],dtype=np.float64)


def _matrix(path):
    m=path.inclusiveMatrix()
    return np.asarray([[m.getElement(i,j) for j in range(4)] for i in range(4)],dtype=np.float64)


def mesh_signature(path,mesh,points,median,spacing):
    name=path.fullPathName()
    counts,vertices=mesh.getVertices();tri_counts,tri_vertices=mesh.getTriangles()
    return fingerprint([name,cmds.ls(name,uuid=True),float(median),float(spacing),
        _points(points),_points(mesh.getPoints(om.MSpace.kObject)),_matrix(path),
        np.asarray(counts,dtype=np.int64),np.asarray(vertices,dtype=np.int64),
        np.asarray(tri_counts,dtype=np.int64),np.asarray(tri_vertices,dtype=np.int64)], sort_keys=True)


def _key(mesh_key,path,curve):
    name=path.fullPathName()
    return (mesh_key,fingerprint([name,cmds.ls(name,uuid=True),
        (curve.degree,curve.form),_points(curve.cvPositions(om.MSpace.kObject)),
        _points(curve.cvPositions(om.MSpace.kWorld)),_matrix(path),
        np.asarray(curve.knots(),dtype=np.float64)], sort_keys=True))


def sample_if_unchanged(mesh_key,path,curve,compute):
    """compute returns (anchors, [(MPoint, face, parameter)], max_offset)."""
    if os.environ.get('WCT_SURFACE_SAMPLE_CACHE','1')=='0':return compute()
    try:
        key=_key(mesh_key,path,curve)
    except Exception as exc:
        log.info('[WCT SampleCache V17.8.14] uncached: '+str(exc))
        return compute()
    saved=_CACHE.get(key)
    if saved is not None:
        _CACHE.move_to_end(key)
        anchors,rows,offset=saved
        log.info('[WCT SampleCache V17.8.14] hit '+path.fullPathName())
        return (list(anchors),[(om.MPoint(x,y,z,w),face,param)
                for x,y,z,w,face,param in rows],offset)
    # Never commit a failed/incomplete sampling operation.
    result=compute()
    anchors,samples,offset=result
    rows=tuple((float(p.x),float(p.y),float(p.z),float(p.w),int(face),float(param))
               for p,face,param in samples)
    _CACHE[key]=(tuple(anchors),rows,float(offset))
    _CACHE.move_to_end(key)
    while len(_CACHE)>_LIMIT:_CACHE.popitem(last=False)
    log.info('[WCT SampleCache V17.8.14] miss '+path.fullPathName())
    return result
