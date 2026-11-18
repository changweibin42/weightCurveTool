# -*- coding: utf-8 -*-
"""Read-only audit of all mesh acceleration combinations, original tolerances.
No scene geometry, selection, or skin weights are written.
"""
import os
import time
import numpy as np
import maya.cmds as cmds
from weightCurveTool import core
from weightCurveTool.tools import StrictCurveRegion as S
from weightCurveTool.tools import NativeEdgeCuts as N
from weightCurveTool.tools.WeightColorPipeline import _active_boundary_curve_names

plugin='wctSurfaceCurve'
version=cmds.pluginInfo(plugin,q=True,version=True)
if version!='1.8.2':
    raise RuntimeError('This audit requires plugin 1.8.2; loaded version is '+version)
ctx=core._ctx
mesh=ctx.continuous_mesh or ctx.group_mgr.find_mesh_from_active()
curves=_active_boundary_curve_names(ctx)
if not mesh or not curves or len(curves)!=3:
    raise RuntimeError('Activate the complete main/FWD/BWD curve set first')
region=S.build_strict_region(mesh,tuple(curves))
eps=max(region['median']*S.STRICT_EPS_FACTOR,1e-10)
keys=('WCT_NATIVE_EDGE_CUTS','WCT_NATIVE_ACCEL_POINT','WCT_NATIVE_ACCEL_NORMAL','WCT_NATIVE_BVH_POINT','WCT_NATIVE_FACE_QUERY','WCT_NATIVE_CURVE_CACHE')
previous={key:os.environ.get(key) for key in keys}
completed=False
try:
    os.environ['WCT_NATIVE_EDGE_CUTS']='0'
    start=time.perf_counter();expected=S.weight_coordinates(region,mesh);python_seconds=time.perf_counter()-start
    base_edges=[(a,b,length,list(cuts)) for a,b,length,cuts in region['field_edges']]
    base_fixed=dict(region['field_fixed']);base_field=region['decay_field'].copy()
    os.environ['WCT_NATIVE_CURVE_CACHE']='0'
    N.clear_cache()
    results=[]
    for label,point_accel,normal_accel,face_bvh in (('P0N0',0,0,0),('P0N1',0,1,0),('FACEN0',0,0,1),('FACEN1',0,1,1)):
        os.environ['WCT_NATIVE_EDGE_CUTS']='1'
        os.environ['WCT_NATIVE_ACCEL_POINT']=str(point_accel)
        os.environ['WCT_NATIVE_ACCEL_NORMAL']=str(normal_accel)
        os.environ['WCT_NATIVE_BVH_POINT']='0'
        os.environ['WCT_NATIVE_FACE_QUERY']='0'
        os.environ['WCT_NATIVE_CURVE_CACHE']='0'
        os.environ['WCT_NATIVE_FACE_QUERY']=str(face_bvh)
        start=time.perf_counter()
        try:
            actual=S.weight_coordinates(region,mesh)
            elapsed=time.perf_counter()-start
            if not region.get('field_native_backend_used'):
                raise RuntimeError('Native command fell back to Python')
            problems=[];max_cut_error=0.;max_field_error=float(np.max(np.abs(region['decay_field']-base_field)))
            current=region['field_edges']
            if len(current)!=len(base_edges):
                problems.append('edge_count')
            else:
                for left,right in zip(base_edges,current):
                    a,b,length,cuts=left
                    if (a,b)!=right[:2]:
                        problems.append(('edge_order',a,b));continue
                    if len(cuts)!=len(right[3]):
                        problems.append(('cut_count',a,b,len(cuts),len(right[3])));continue
                    for (u,value),(v,other) in zip(cuts,right[3]):
                        error=abs(u-v)*length;max_cut_error=max(max_cut_error,error)
                        if abs(value-other)>=1e-12 or error>eps*4:
                            problems.append(('cut',a,b,'value',value,'u',u,'native_u',v,'world_error',error,'limit',eps*4))
            if base_fixed!=region['field_fixed']:problems.append('fixed_vertices')
            if not np.allclose(region['decay_field'],base_field,atol=1e-8,rtol=0):problems.append(('field',max_field_error))
            if not np.allclose(actual[0],expected[0],atol=1e-8,rtol=0):problems.append('decay_coordinates')
            if not np.array_equal(actual[1],expected[1]):problems.append('side_labels')
            if not np.array_equal(actual[2],expected[2]):problems.append('ridge_labels')
            passed=not problems
            result=dict(label=label,point=point_accel,normal=normal_accel,face_bvh=face_bvh,passed=passed,
                        seconds=elapsed,max_cut_error=max_cut_error,max_field_error=max_field_error,
                        mismatches=len(problems),details=problems[:8])
            results.append(result)
            print('[WCT ACCEL AUDIT] {} {} seconds={:.3f} max_cut_world_error={:.12g} max_field_error={:.12g} mismatches={}'.format(
                label,'PASS' if passed else 'FAIL',elapsed,max_cut_error,max_field_error,len(problems)))
            if problems:print('[WCT ACCEL DETAILS]',problems[:8])
        except Exception as exc:
            result=dict(label=label,point=point_accel,normal=normal_accel,face_bvh=face_bvh,passed=False,
                        seconds=time.perf_counter()-start,error=repr(exc))
            results.append(result);print('[WCT ACCEL AUDIT] {} ERROR {}'.format(label,repr(exc)))
    passing=[r for r in results if r['passed']]
    # Even the no-acceleration command must match before trusting other modes.
    baseline=next((r for r in results if r['label']=='P0N0'),None)
    if not baseline or not baseline['passed']:
        raise RuntimeError('Unaccelerated C++ no longer matches Python; no mode enabled')
    best=min(passing,key=lambda r:r['seconds']) if passing else None
    if best is not None and best['seconds']<python_seconds:
        os.environ['WCT_NATIVE_EDGE_CUTS']='1'
        os.environ['WCT_NATIVE_ACCEL_POINT']=str(best['point'])
        os.environ['WCT_NATIVE_ACCEL_NORMAL']=str(best['normal'])
        os.environ['WCT_NATIVE_BVH_POINT']='0'
        os.environ['WCT_NATIVE_FACE_QUERY']='0'
        os.environ['WCT_NATIVE_CURVE_CACHE']='0'
        os.environ['WCT_NATIVE_FACE_QUERY']=str(best['face_bvh'])
        print('[WCT ACCEL SELECTED] {} python={:.3f}s native={:.3f}s; enabled for this session/current tested geometry'.format(best['label'],python_seconds,best['seconds']))
    else:
        os.environ['WCT_NATIVE_EDGE_CUTS']='0'
        os.environ['WCT_NATIVE_ACCEL_POINT']='0'
        os.environ['WCT_NATIVE_ACCEL_NORMAL']='0'
        os.environ['WCT_NATIVE_BVH_POINT']='0'
        os.environ['WCT_NATIVE_FACE_QUERY']='0'
        os.environ['WCT_NATIVE_CURVE_CACHE']='0'
        print('[WCT ACCEL SELECTED] Python retained; no validated faster native mode')
    if best is not None and os.environ.get('WCT_NATIVE_EDGE_CUTS')=='1':
        os.environ['WCT_NATIVE_CURVE_CACHE']='1'
        N.clear_cache()
        snapshots=[]
        for phase in ('cold','warm'):
            start=time.perf_counter();values=S.weight_coordinates(region,mesh)
            elapsed=time.perf_counter()-start
            np.testing.assert_allclose(region['decay_field'],base_field,atol=1e-8,rtol=0)
            np.testing.assert_allclose(values[0],expected[0],atol=1e-8,rtol=0)
            np.testing.assert_array_equal(values[1],expected[1])
            np.testing.assert_array_equal(values[2],expected[2])
            snapshots.append((list(region['field_edges']),dict(region['field_fixed']),region['decay_field'].copy()))
            print('[WCT INCREMENTAL AUDIT] {} PASS seconds={:.3f}'.format(phase,elapsed))
        assert snapshots[0][0]==snapshots[1][0],'Warm cache changed edge constraints'
        assert snapshots[0][1]==snapshots[1][1],'Warm cache changed fixed constraints'
        np.testing.assert_array_equal(snapshots[0][2],snapshots[1][2])
    completed=True
    print('[WCT ACCEL AUDIT END] No scene weights or curves changed. Results remain in variable results.')
finally:
    if not completed:
        # A failed audit must not leave a previously enabled acceleration mode active.
        os.environ['WCT_NATIVE_EDGE_CUTS']='0'
        os.environ['WCT_NATIVE_ACCEL_POINT']='0'
        os.environ['WCT_NATIVE_ACCEL_NORMAL']='0'
        os.environ['WCT_NATIVE_BVH_POINT']='0'
        os.environ['WCT_NATIVE_FACE_QUERY']='0'
        os.environ['WCT_NATIVE_CURVE_CACHE']='0'
