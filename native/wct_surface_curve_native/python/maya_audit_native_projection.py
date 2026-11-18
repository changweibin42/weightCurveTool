# -*- coding: utf-8 -*-
"""Read-only curve-pipeline projection audit. Only passing preferences persist.
Rebuilds point arrays in memory, never creates/replaces curves or writes skin.
"""
import os
import time
import statistics
import numpy as np
import maya.cmds as cmds
from weightCurveTool import core
from weightCurveTool.tools import CurveRefine as CR
from weightCurveTool.tools import NativeProjection as P
from weightCurveTool.tools.MayaSelectors import _loc_sort_key

identity=P.binary_identity()
ctx=core._ctx;surface=ctx.rest_surface;builder=ctx.curve_builder
if surface is None or builder is None:raise RuntimeError('Create/activate the complete curve set first')
if builder.rest_surface is not surface:raise RuntimeError('Activate Modify Curves to synchronize the reference surface first')
mesh=ctx.continuous_mesh or ctx.group_mgr.find_mesh_from_active()
shape=(cmds.listRelatives(mesh,shapes=True,type='mesh') or [None])[0]
if shape is None:raise RuntimeError('No active mesh')
closed=bool(ctx.active_closed)
keys=('WCT_NATIVE_PROJECT','WCT_NATIVE_PROJECT_AUDIT','WCT_NATIVE_PROJECT_MODE')
previous={key:os.environ.get(key) for key in keys}
sentinel=object();old_instance=surface.__dict__.get('project_to_surface',sentinel)
original=surface.project_to_surface
records=[]
def observe(points,use_mesh_api=True):
    arr=np.asarray(points,dtype=float);start=time.perf_counter()
    result=original(points,use_mesh_api=use_mesh_api)
    if use_mesh_api:
        records.append(dict(points=arr.copy(),result=np.asarray(result,dtype=float).copy(),seconds=time.perf_counter()-start))
    return result
surface.project_to_surface=observe

def derive_curves():
    outputs={}
    for kind in ('main','fwd','bwd'):
        CR.clear_mesh_fn_cache()
        joints=sorted(ctx.group_mgr.get_joints_from_active(kind) or [],key=_loc_sort_key)
        if len(joints)<2:raise RuntimeError('Missing active '+kind+' controls')
        raw=builder._read_joint_rest_positions(joints,mesh)
        if raw is None:raise RuntimeError('Controls lack reference bindings')
        points=builder._boundary_points(joints,raw,closed)
        if points is None:
            build=builder._build_path_gaussian if builder.display_mode=='gaussian' else builder._build_path_laplacian
            dense,anchors=build(shape,raw,closed)
            if dense is None or anchors is None:raise RuntimeError('Cannot build '+kind+' path')
            points=CR.simplify_by_anchors(dense,anchors,builder.internal_per_segment)
            if closed and len(points)>=4 and np.linalg.norm(np.asarray(points[0])-np.asarray(points[-1]))<1e-6:
                points=points[:-1]
        local=np.asarray(points,dtype=float)
        center=None
        if len(raw)>=3:
            fit=CR.fit_circle_3d(raw)
            if fit is not None:center=fit[0]
        if center is None:center=np.mean(np.asarray(raw,dtype=float),axis=0)
        display=np.asarray(builder._apply_offset(shape,points,center),dtype=float)
        cvs=np.asarray(CR.ep_to_cv(display),dtype=float) if closed and len(display)>=4 else display.copy()
        outputs[kind]=dict(local=local,display=display,cvs=cvs)
    return outputs

def calculate():
    records.clear();before=P._BATCHES;failures=P._FALLBACKS
    outputs=derive_curves()
    return dict(outputs=outputs,records=list(records),seconds=sum(r['seconds'] for r in records),
                batches=P._BATCHES-before,fallbacks=P._FALLBACKS-failures)

results=[]
try:
    os.environ['WCT_NATIVE_PROJECT']='0'
    baseline=[calculate() for _ in range(3)]
    expected=baseline[-1];python_seconds=statistics.median(row['seconds'] for row in baseline)
    if not expected['records']:raise RuntimeError('No reference-mesh projection calls captured; activate the current curve set first')
    print('[WCT Projection Audit] Python project median={:.4f}s batches={}'.format(python_seconds,len(expected['records'])))
    for row in baseline:
        for kind in expected['outputs']:
            for name in ('local','display','cvs'):
                if not np.array_equal(row['outputs'][kind][name],expected['outputs'][kind][name]):
                    raise RuntimeError('Python curve pipeline is not exactly repeatable: '+kind+'/'+name)
    for mode in (0,2):
        os.environ['WCT_NATIVE_PROJECT']='1';os.environ['WCT_NATIVE_PROJECT_AUDIT']='1';os.environ['WCT_NATIVE_PROJECT_MODE']=str(mode)
        try:
            trials=[calculate() for _ in range(3)];problems=[];max_error=0.
            for trial in trials:
                if trial['fallbacks'] or trial['batches']!=len(expected['records']):problems.append('native incomplete/fallback')
                if len(trial['records'])!=len(expected['records']):problems.append('projection call count');continue
                for i,(a,b) in enumerate(zip(expected['records'],trial['records'])):
                    if not np.array_equal(a['points'],b['points']):problems.append(('projection input',i))
                    if a['result'].shape!=b['result'].shape:problems.append(('projection shape',i));continue
                    error=float(np.max(np.abs(a['result']-b['result']))) if a['result'].size else 0.
                    max_error=max(max_error,error)
                    if not np.array_equal(a['result'],b['result']):problems.append(('projected coordinate',i,error))
                for kind in expected['outputs']:
                    for name in ('local','display','cvs'):
                        if not np.array_equal(trial['outputs'][kind][name],expected['outputs'][kind][name]):
                            problems.append(('curve point array',kind,name))
            seconds=statistics.median(trial['seconds'] for trial in trials)
            row=dict(mode=mode,passed=not problems,seconds=seconds,max_error=max_error,details=problems[:12]);results.append(row)
            print('[WCT Projection Audit] mode={} {} project_median={:.4f}s max_error={:.12g}'.format(mode,'PASS' if row['passed'] else 'FAIL',seconds,max_error))
            if problems:print('[WCT Projection Audit Details]',problems[:12])
        except Exception as exc:
            results.append(dict(mode=mode,passed=False,error=repr(exc)));print('[WCT Projection Audit] mode={} ERROR {}'.format(mode,repr(exc)))
    legacy=next(r for r in results if r['mode']==0);passing=[r for r in results if r['passed']]
    best=min(passing,key=lambda row:row['seconds']) if legacy['passed'] and passing else None
    if best is not None and best['seconds']<python_seconds:
        P.remember_audit(best['mode']);print('[WCT Projection Audit] SELECTED mode={} for binary {}'.format(best['mode'],identity['sha256'][:12]))
    else:P.clear_audit();print('[WCT Projection Audit] Python retained')
finally:
    if old_instance is sentinel:del surface.project_to_surface
    else:surface.project_to_surface=old_instance
    for key,value in previous.items():
        if value is None:os.environ.pop(key,None)
        else:os.environ[key]=value
print('[WCT Projection Audit END] No curves/weights/selection changed. Results are in variable results.')
