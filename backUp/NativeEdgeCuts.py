# -*- coding: utf-8 -*-
"""Optional read-only C++ command backend. Enable after the Maya parity audit."""
import os
import hashlib
import json
from collections import OrderedDict
import maya.api.OpenMaya as om
from time import perf_counter
import numpy as np
import maya.cmds as cmds

def _shape(name,kind):
    matches=cmds.ls(name,long=True) or []
    if len(matches)!=1:
        raise RuntimeError('Missing or ambiguous native input: '+name)
    name=matches[0]
    if cmds.nodeType(name)!=kind:
        matches=cmds.listRelatives(name,shapes=True,noIntermediate=True,fullPath=True,type=kind) or []
        if len(matches)!=1:
            raise RuntimeError('Expected one '+kind+' shape: '+name)
        name=matches[0]
    return name

def _batch(region, mesh_name, eps, mask=7, partial=False):
    edges=[(a,b,float(length)) for a,neighbors in enumerate(region['graph'])
           for b,length in neighbors.items() if b>a]
    chunks=[('2 {} {} {}'.format(len(region['points']),len(edges),mask) if partial else '1 {} {}'.format(len(region['points']),len(edges)))]
    chunks.extend('{} {} {:.17g}'.format(a,b,length) for a,b,length in edges)
    for name in region['curves']:
        samples=region['curve_samples'][name]
        chunks.append(str(len(samples)))
        chunks.extend('{:.17g} {:.17g} {:.17g} {}'.format(float(p.x),float(p.y),float(p.z),int(face))
                      for p,face,_ in samples)
    values=cmds.wctCurveEdgeCuts(_shape(mesh_name,'mesh'),
                                *[_shape(c,'nurbsCurve') for c in region['curves']],float(eps),
                                float(region['median']),4,' '.join(chunks))
    records=np.asarray(values,dtype=float).reshape(-1,6)
    if not np.all(np.isfinite(records)):
        raise RuntimeError('Non-finite native result')
    if len(records)<1 or tuple(records[-1,:4])!=(2.,1.,float(len(edges)),float(len(region['points']))):
        raise RuntimeError('Native result protocol/version mismatch')
    cuts={};endpoints={name:{} for name in region['curves']}
    for kind,a,b,c,d,e in records[:-1]:
        if kind==0:
            if a!=int(a) or not 0<=int(a)<len(edges) or not 0<=b<=1 or c not in (.5,1.,0.):
                raise RuntimeError('Invalid native edge cut')
            x,y,_=edges[int(a)];cuts.setdefault((x,y),[]).append((float(b),float(c)))
        elif kind==1:
            if a!=int(a) or not 0<=int(a)<3 or b!=int(b) or not 0<=int(b)<len(region['points']) or c not in (0.,1.):
                raise RuntimeError('Invalid native endpoint')
            endpoints[region['curves'][int(a)]][int(b)]=(float(d),float(e)) if c else None
        else:
            raise RuntimeError('Unknown native result record')
    return cuts,endpoints


def compute_native(region, mesh_name, eps):
    """Direct uncached full command, retained for diagnostics."""
    return _batch(region, mesh_name, eps)


def compute_if_enabled(region, mesh_name, eps):
    region.pop('_native_endpoint_cache',None)
    region['field_native_backend_used']=False
    if os.environ.get('WCT_NATIVE_EDGE_CUTS','0')!='1':
        return None
    try:
        started=perf_counter()
        cuts,endpoints,hits=_cached(region,mesh_name,eps)
        region['_native_endpoint_cache']=endpoints
        region['field_native_backend_used']=True
        print('[WCT Native Cuts] batch={:.3f}s cut_edges={} reused_curves={} rebuilt_curves={}'.format(perf_counter()-started,len(cuts),hits,3-hits))
        return cuts
    except Exception as exc:
        # No weights were written by the command; original algorithm is safe.
        print('[WCT Native Cuts] fallback to Python: {}'.format(exc))
        return None


_CACHE = OrderedDict()
_CACHE_LIMIT = 24
_MODE_KEYS = ('WCT_NATIVE_ACCEL_POINT','WCT_NATIVE_ACCEL_NORMAL',
              'WCT_NATIVE_BVH_POINT','WCT_NATIVE_FACE_QUERY')


def clear_cache():
    _CACHE.clear()


def _hash(parts):
    h=hashlib.blake2b(digest_size=32)
    for part in parts:
        if isinstance(part,np.ndarray):
            value=np.ascontiguousarray(part)
            h.update(str((value.shape,value.dtype.str)).encode('ascii'))
            h.update(value.tobytes())
        else:
            h.update(json.dumps(part,sort_keys=True,allow_nan=False).encode('utf-8'))
        h.update(b'\0')
    return h.digest()


def _fingerprints(region,mesh_name,eps):
    shape=_shape(mesh_name,'mesh');sel=om.MSelectionList();sel.add(shape);dag=sel.getDagPath(0)
    fn=om.MFnMesh(dag);matrix=dag.inclusiveMatrix()
    def points(space):
        return np.asarray([(p.x,p.y,p.z) for p in fn.getPoints(space)],dtype=np.float64)
    counts,connects=fn.getVertices();tri_counts,tri_vertices=fn.getTriangles()
    normals=np.asarray([(n.x,n.y,n.z) for n in fn.getNormals(om.MSpace.kWorld)],dtype=np.float64)
    normal_counts,normal_ids=fn.getNormalIds()
    edges=np.asarray([(a,b,length) for a,ns in enumerate(region['graph']) for b,length in ns.items() if b>a],dtype=np.float64)
    common=_hash([shape,cmds.ls(shape,uuid=True),float(eps),float(region['median']),4,
                  tuple(os.environ.get(k,'0') for k in _MODE_KEYS),
                  points(om.MSpace.kObject),points(om.MSpace.kWorld),
                  np.asarray([[matrix.getElement(i,j) for j in range(4)] for i in range(4)]),
                  np.asarray(counts,dtype=np.int64),np.asarray(connects,dtype=np.int64),
                  np.asarray(tri_counts,dtype=np.int64),np.asarray(tri_vertices,dtype=np.int64),
                  normals,np.asarray(normal_counts,dtype=np.int64),np.asarray(normal_ids,dtype=np.int64),edges])
    keys=[]
    for ci,name in enumerate(region['curves']):
        curve_shape=_shape(name,'nurbsCurve');sel=om.MSelectionList();sel.add(curve_shape);curve_dag=sel.getDagPath(0);curve=om.MFnNurbsCurve(curve_dag)
        cvs=np.asarray([(p.x,p.y,p.z,p.w) for p in curve.cvPositions(om.MSpace.kWorld)],dtype=np.float64)
        # Projected samples control local-fold fallback and candidate topology.
        samples=np.asarray([(p.x,p.y,p.z,face,param) for p,face,param in region['curve_samples'][name]],dtype=np.float64)
        object_cvs=np.asarray([(p.x,p.y,p.z,p.w) for p in curve.cvPositions(om.MSpace.kObject)],dtype=np.float64)
        curve_matrix=curve_dag.inclusiveMatrix()
        curve_matrix=np.asarray([[curve_matrix.getElement(i,j) for j in range(4)] for i in range(4)])
        keys.append((common,ci,_hash([curve_shape,cmds.ls(curve_shape,uuid=True),
                    (curve.degree,curve.form),cvs,object_cvs,curve_matrix,np.asarray(curve.knots(),dtype=np.float64),samples])))
    return keys


def _cached(region,mesh_name,eps):
    if os.environ.get('WCT_NATIVE_CURVE_CACHE','1')!='1':
        cuts,endpoints=compute_native(region,mesh_name,eps)
        return cuts,endpoints,0
    # A protocol-1 binary is safe to use, but cannot compute only selected curves.
    version=tuple(int(v) for v in cmds.pluginInfo('wctSurfaceCurve',q=True,version=True).split('.')[:3])
    if version<(1,7,0):
        cuts,endpoints=compute_native(region,mesh_name,eps)
        return cuts,endpoints,0
    keys=_fingerprints(region,mesh_name,eps)
    values=[_CACHE.get(key) for key in keys]
    missing=[i for i,value in enumerate(values) if value is None]
    if missing:
        mask=sum(1<<i for i in missing)
        cuts,endpoints=_batch(region,mesh_name,eps,mask=mask,partial=True)
        # Commit cache records only after the entire requested batch was decoded.
        for i in missing:
            value=(.5,1.,0.)[i]
            selected={edge:[(u,v) for u,v in rows if v==value]
                      for edge,rows in cuts.items() if any(v==value for _,v in rows)}
            values[i]=(selected,dict(endpoints[region['curves'][i]]))
        for i in missing:
            _CACHE[keys[i]]=values[i]
    merged={};all_endpoints={}
    for i,(cuts,endpoints) in enumerate(values):
        for edge,rows in cuts.items():merged.setdefault(edge,[]).extend(rows)
        all_endpoints[region['curves'][i]]=dict(endpoints)
        _CACHE.move_to_end(keys[i])
    while len(_CACHE)>_CACHE_LIMIT:_CACHE.popitem(last=False)
    return merged,all_endpoints,3-len(missing)
