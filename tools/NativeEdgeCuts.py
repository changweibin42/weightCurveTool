# -*- coding: utf-8 -*-
"""V17.8.13: stable diagnostics across rebuilt curve UUIDs; V17.8.11 defaults.

Explicit environment overrides remain authoritative. Missing/older plugins
and failed native queries retain the original Python fallback.
"""
import os
import json
from collections import OrderedDict
import maya.api.OpenMaya as om
from time import perf_counter
import numpy as np
import maya.cmds as cmds

from ..Logger import log, run_native_command

from .CacheFingerprint import fingerprint

try:
    from .NativeQueryReuse import mode_if_enabled as _query_reuse_mode
except ImportError:
    def _query_reuse_mode():
        return 0

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

def _batch(region, mesh_name, eps, mask=7, partial=False, query_reuse=None):
    edges=[(a,b,float(length)) for a,neighbors in enumerate(region['graph'])
           for b,length in neighbors.items() if b>a]
    chunks=[('2 {} {} {}'.format(len(region['points']),len(edges),mask) if partial else '1 {} {}'.format(len(region['points']),len(edges)))]
    chunks.extend('{} {} {:.17g}'.format(a,b,length) for a,b,length in edges)
    for name in region['curves']:
        samples=region['curve_samples'][name]
        chunks.append(str(len(samples)))
        chunks.extend('{:.17g} {:.17g} {:.17g} {}'.format(float(p.x),float(p.y),float(p.z),int(face))
                      for p,face,_ in samples)
    reuse=_query_reuse_mode() if query_reuse is None else int(query_reuse)
    if reuse not in (0,1):
        raise ValueError('Query reuse must be 0 or 1')
    arguments=(_shape(mesh_name,'mesh'),
               *[_shape(c,'nurbsCurve') for c in region['curves']],float(eps),
               float(region['median']),4,' '.join(chunks))
    # Preserve the original eight-argument ABI when the new mode is disabled.
    values=run_native_command(cmds.wctCurveEdgeCuts, *(arguments+(reuse,) if reuse else arguments))
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


_BACKEND_NOTICE = None


def _prepare_backend():
    """Enable FACEN1 defaults lazily; never turn an explicit opt-out on."""
    global _BACKEND_NOTICE
    enabled=os.environ.get('WCT_NATIVE_EDGE_CUTS')
    if enabled is not None and enabled!='1':
        message='disabled by WCT_NATIVE_EDGE_CUTS='+enabled
        if log.details_enabled() and _BACKEND_NOTICE!=message:
            log.info('[WCT Native Backend V17.8.11] '+message)
            _BACKEND_NOTICE=message
        return False
    # Only load the binary shipped in this package, never an arbitrary copy
    # found by basename on MAYA_PLUG_IN_PATH.
    try:
        try:
            loaded=bool(cmds.pluginInfo('wctSurfaceCurve',q=True,loaded=True))
        except RuntimeError:
            loaded=False
        if not loaded:
            path=os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              'plug-ins','wctSurfaceCurve.mll')
            if not os.path.isfile(path):
                raise RuntimeError('wctSurfaceCurve is not loaded; package binary not found')
            cmds.loadPlugin(path,quiet=True)
        text=cmds.pluginInfo('wctSurfaceCurve',q=True,version=True)
        version=tuple(int(v) for v in text.split('.')[:3])
        if version<(1,7,0):
            raise RuntimeError('wctSurfaceCurve 1.7.0 or newer is required; loaded '+text)
        commands=cmds.pluginInfo('wctSurfaceCurve',q=True,command=True) or []
        if 'wctCurveEdgeCuts' not in commands:
            raise RuntimeError('wctCurveEdgeCuts command is unavailable')
    except Exception as exc:
        message=str(exc)
        if log.details_enabled() and _BACKEND_NOTICE!=message:
            log.info('[WCT Native Backend V17.8.11] Python fallback: '+message)
            _BACKEND_NOTICE=message
        return False
    # These are process-local defaults, recreated after a Maya restart.
    # Audits and explicit debugging flags can still request any other mode.
    for key,value in (('WCT_NATIVE_ACCEL_POINT','0'),
                      ('WCT_NATIVE_ACCEL_NORMAL','1'),
                      ('WCT_NATIVE_BVH_POINT','0'),
                      ('WCT_NATIVE_FACE_QUERY','1'),
                      ('WCT_NATIVE_CURVE_CACHE','1')):
        os.environ.setdefault(key,value)
    if log.details_enabled() and _BACKEND_NOTICE!='native':
        log.info('[WCT Native Backend V17.8.11] enabled; plugin='+text+
              ' face_query='+os.environ['WCT_NATIVE_FACE_QUERY']+
              ' normal='+os.environ['WCT_NATIVE_ACCEL_NORMAL']+
              ' curve_cache='+os.environ['WCT_NATIVE_CURVE_CACHE'])
        _BACKEND_NOTICE='native'
    return True


def compute_if_enabled(region, mesh_name, eps):
    region.pop('_native_endpoint_cache',None)
    region['field_native_backend_used']=False
    if not _prepare_backend():
        return None
    try:
        started=perf_counter()
        cuts,endpoints,hits=_cached(region,mesh_name,eps)
        region['_native_endpoint_cache']=endpoints
        region['field_native_backend_used']=True
        log.info('[WCT Native Cuts] batch={:.3f}s cut_edges={} reused_curves={} rebuilt_curves={}'.format(perf_counter()-started,len(cuts),hits,3-hits))
        return cuts
    except Exception as exc:
        # No weights were written by the command; original algorithm is safe.
        log.info('[WCT Native Cuts] fallback to Python: {}'.format(exc))
        return None


_CACHE = OrderedDict()
_CACHE_LIMIT = 24
_TRACE = OrderedDict()
_TRACE_LIMIT = 4
_MODE_KEYS = ('WCT_NATIVE_ACCEL_POINT','WCT_NATIVE_ACCEL_NORMAL',
              'WCT_NATIVE_BVH_POINT','WCT_NATIVE_FACE_QUERY')


def clear_cache():
    _CACHE.clear()
    _TRACE.clear()


def _trace_fields(previous,current):
    """Report exact changes, without using diagnostics to decide reuse."""
    changed=[]
    for name,value in current.items():
        old=previous.get(name)
        if old is not None and fingerprint([old], sort_keys=True)==fingerprint([value], sort_keys=True):continue
        entry={'field':name}
        if isinstance(value,np.ndarray) and isinstance(old,np.ndarray):
            entry['shape_before']=list(old.shape);entry['shape_after']=list(value.shape)
            entry['dtype_before']=old.dtype.str;entry['dtype_after']=value.dtype.str
            if old.shape==value.shape:
                different=old!=value
                entry['changed_values']=int(np.count_nonzero(different))
                if value.ndim:
                    entry['changed_rows']=int(np.count_nonzero(
                        np.any(different.reshape(value.shape[0],-1),axis=1))) if value.size else 0
                if value.size:
                    delta=np.abs(value.astype(np.float64)-old.astype(np.float64))
                    entry['max_abs_delta']=float(np.max(delta))
        else:
            entry['before']=old;entry['after']=value
        changed.append(entry)
    return changed


def _trace_fingerprints(region,common_fields,curve_fields,keys):
    if not log.details_enabled() or os.environ.get('WCT_NATIVE_CACHE_TRACE','1')=='0':return
    started=perf_counter()
    # The builder deletes/recreates the moved curve, so its UUID changes.
    # Group diagnostic history by stable full DAG slots, then report UUID
    # changes as input differences. Actual cache keys STILL include UUIDs.
    identity=(common_fields['mesh_shape'],
              tuple(fields['curve_shape'] for fields in curve_fields))
    previous=_TRACE.get(identity)
    shared=_trace_fields(previous[0],common_fields) if previous else []
    rows=[]
    for i,(fields,key) in enumerate(zip(curve_fields,keys)):
        local=_trace_fields(previous[1][i],fields) if previous else []
        hit=key in _CACHE
        reason=('hit' if hit else 'cold' if previous is None else
                'inputs_changed' if shared or local else 'entry_evicted_or_not_committed')
        rows.append(dict(curve=('main','fwd','bwd')[i],cache=reason,changed=local))
    report=dict(version='17.8.13',common_changed=shared,curves=rows)
    region['field_native_cache_trace']=report
    if previous is None:
        log.info('[WCT CacheTrace V17.8.13] cold: first observation of these mesh/curve paths')
    if shared:
        log.info('[WCT CacheTrace V17.8.13] common '+json.dumps(shared,ensure_ascii=True))
    for row in rows:
        log.info('[WCT CacheTrace V17.8.13] '+json.dumps(row,ensure_ascii=True))
    # Snapshots belong only to diagnostics. Exact cache keys/records and
    # backend calculations never read these values.
    def snapshot(fields):
        return {k:v.copy() if isinstance(v,np.ndarray) else v for k,v in fields.items()}
    _TRACE[identity]=(snapshot(common_fields),[snapshot(f) for f in curve_fields])
    _TRACE.move_to_end(identity)
    while len(_TRACE)>_TRACE_LIMIT:_TRACE.popitem(last=False)
    region['field_native_cache_trace_seconds']=perf_counter()-started


def _fingerprints(region,mesh_name,eps):
    shape=_shape(mesh_name,'mesh');sel=om.MSelectionList();sel.add(shape);dag=sel.getDagPath(0)
    fn=om.MFnMesh(dag);matrix=dag.inclusiveMatrix()
    def points(space):
        return np.asarray([(p.x,p.y,p.z) for p in fn.getPoints(space)],dtype=np.float64)
    counts,connects=fn.getVertices();tri_counts,tri_vertices=fn.getTriangles()
    normals=np.asarray([(n.x,n.y,n.z) for n in fn.getNormals(om.MSpace.kWorld)],dtype=np.float64)
    normal_counts,normal_ids=fn.getNormalIds()
    edges=np.asarray([(a,b,length) for a,ns in enumerate(region['graph']) for b,length in ns.items() if b>a],dtype=np.float64)
    object_points=points(om.MSpace.kObject);world_points=points(om.MSpace.kWorld)
    mesh_matrix=np.asarray([[matrix.getElement(i,j) for j in range(4)] for i in range(4)])
    counts=np.asarray(counts,dtype=np.int64);connects=np.asarray(connects,dtype=np.int64)
    tri_counts=np.asarray(tri_counts,dtype=np.int64);tri_vertices=np.asarray(tri_vertices,dtype=np.int64)
    normal_counts=np.asarray(normal_counts,dtype=np.int64);normal_ids=np.asarray(normal_ids,dtype=np.int64)
    mesh_uuid=cmds.ls(shape,uuid=True)
    modes=tuple(os.environ.get(k,'0') for k in _MODE_KEYS)+(str(_query_reuse_mode()),)
    # Preserve the exact original digest sequence and bytes.
    common=fingerprint([shape,mesh_uuid,float(eps),float(region['median']),4,modes,
                  object_points,world_points,mesh_matrix,counts,connects,
                  tri_counts,tri_vertices,normals,normal_counts,normal_ids,edges], sort_keys=True)
    common_fields=dict(mesh_shape=shape,mesh_uuid=mesh_uuid,epsilon=float(eps),
                       median=float(region['median']),query_modes=modes,
                       mesh_object_points=object_points,mesh_world_points=world_points,
                       mesh_matrix=mesh_matrix,polygon_counts=counts,polygon_vertices=connects,
                       triangle_counts=tri_counts,triangle_vertices=tri_vertices,
                       world_normals=normals,normal_counts=normal_counts,normal_ids=normal_ids,
                       graph_edges=edges)
    keys=[];curve_fields=[]
    for ci,name in enumerate(region['curves']):
        curve_shape=_shape(name,'nurbsCurve');sel=om.MSelectionList();sel.add(curve_shape);curve_dag=sel.getDagPath(0);curve=om.MFnNurbsCurve(curve_dag)
        cvs=np.asarray([(p.x,p.y,p.z,p.w) for p in curve.cvPositions(om.MSpace.kWorld)],dtype=np.float64)
        # Projected samples control local-fold fallback and candidate topology.
        samples=np.asarray([(p.x,p.y,p.z,face,param) for p,face,param in region['curve_samples'][name]],dtype=np.float64)
        object_cvs=np.asarray([(p.x,p.y,p.z,p.w) for p in curve.cvPositions(om.MSpace.kObject)],dtype=np.float64)
        curve_matrix=curve_dag.inclusiveMatrix()
        curve_matrix=np.asarray([[curve_matrix.getElement(i,j) for j in range(4)] for i in range(4)])
        curve_uuid=cmds.ls(curve_shape,uuid=True)
        knots=np.asarray(curve.knots(),dtype=np.float64)
        keys.append((common,ci,fingerprint([curve_shape,curve_uuid,
                    (curve.degree,curve.form),cvs,object_cvs,curve_matrix,knots,samples], sort_keys=True)))
        curve_fields.append(dict(curve_shape=curve_shape,curve_uuid=curve_uuid,
            degree_form=(curve.degree,curve.form),world_cvs=cvs,object_cvs=object_cvs,
            curve_matrix=curve_matrix,knots=knots,
            sample_positions=samples[:,:3] if samples.size else samples,
            sample_faces=samples[:,3] if samples.size else samples,
            sample_parameters=samples[:,4] if samples.size else samples))
    try:
        _trace_fingerprints(region,common_fields,curve_fields,keys)
    except Exception as exc:
        # Diagnostics must never force a safe native solve onto Python.
        log.info('[WCT CacheTrace V17.8.13] diagnostic unavailable: '+str(exc))
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
