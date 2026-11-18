# -*- coding: utf-8 -*-
"""Read-only geometry/field parity audit; saves preference only on a pass.
Run after opening weightCurveTool and activating a complete curve set.
No skin weights, loc transforms, curves or selections are modified.
"""
import os
import time
import statistics
import numpy as np
import maya.cmds as cmds
from weightCurveTool import core
from weightCurveTool.tools import StrictCurveRegion as S
from weightCurveTool.tools import BoundaryQueryCache as B
from weightCurveTool.tools import NativeStrictQueries as N
from weightCurveTool.tools.WeightColorPipeline import _active_boundary_curve_names

identity=N.binary_identity()
ctx=core._ctx
mesh=ctx.continuous_mesh or ctx.group_mgr.find_mesh_from_active()
curves=tuple(_active_boundary_curve_names(ctx) or ())
if not mesh or len(curves)!=3:raise RuntimeError('Activate one complete ridge/FWD/BWD set first')
keys=('WCT_NATIVE_STRICT_QUERIES','WCT_NATIVE_STRICT_AUDIT','WCT_NATIVE_STRICT_MODE',
      'WCT_BOUNDARY_QUERY_CACHE','WCT_NATIVE_CACHE_TRACE')
previous={key:os.environ.get(key) for key in keys}
original_prepare=S._prepare_boundary_queries
sessions=[]
def observed_prepare(*args,**kwargs):
    session=B.prepare_queries(*args,**kwargs);sessions.append(session);return session
S._prepare_boundary_queries=observed_prepare
def calculate():
    B.clear_cache();sessions.clear()
    start=time.perf_counter();region=S.build_strict_region(mesh,curves)
    region_seconds=time.perf_counter()-start
    coordinates=S.weight_coordinates(region,mesh)
    raw={label:dict(values) for label,key,values,probe in sessions[-1].rows}
    native=sum(probe.native_vertices for label,key,values,probe in sessions[-1].rows)
    return region,coordinates,raw,region_seconds,native
results=[];selected=None
try:
    os.environ['WCT_BOUNDARY_QUERY_CACHE']='0'
    os.environ['WCT_NATIVE_CACHE_TRACE']='0'
    os.environ['WCT_NATIVE_STRICT_QUERIES']='0'
    baseline_runs=[calculate() for _ in range(3)]
    expected=baseline_runs[-1]
    python_seconds=statistics.median(row[3] for row in baseline_runs)
    print('[WCT Strict Audit] Python median region={:.4f}s (3 runs)'.format(python_seconds))
    tolerance=max(expected[0]['median']*S.STRICT_EPS_FACTOR*.01,1e-12)
    def compare(actual):
        problems=[];max_raw=0.
        for label,values in expected[2].items():
            current=actual[2].get(label,{})
            if set(values)!=set(current):problems.append((label,'raw vertex set'));continue
            for vertex,value in values.items():
                other=current[vertex]
                if (value is None)!=(other is None):problems.append((label,vertex,'None mismatch'))
                elif value is not None:
                    error=abs(value-other);max_raw=max(max_raw,error)
                    if error>tolerance:problems.append((label,vertex,'raw value',error))
        if not actual[4]:problems.append('native strict path was not used')
        for name in ('mask','fwd_outer'):
            if not np.array_equal(expected[0][name],actual[0][name]):problems.append(name)
        max_field=float(np.max(np.abs(expected[0]['decay_field']-actual[0]['decay_field'])))
        if max_field>1e-8:problems.append(('decay field',max_field))
        if not np.allclose(expected[1][0],actual[1][0],atol=1e-8,rtol=0):problems.append('decay coordinates')
        for i in (1,2):
            if not np.array_equal(expected[1][i],actual[1][i]):problems.append(('side/ridge',i))
        if expected[0]['outer_error']!=actual[0]['outer_error']:problems.append('outer error')
        return problems,max_raw,max_field
    for mode in (0,1,2,3):
        os.environ['WCT_NATIVE_STRICT_QUERIES']='1'
        os.environ['WCT_NATIVE_STRICT_AUDIT']='1'
        os.environ['WCT_NATIVE_STRICT_MODE']=str(mode)
        try:
            runs=[calculate() for _ in range(3)]
            checks=[compare(actual) for actual in runs]
            problems=[issue for check in checks for issue in check[0]]
            max_raw=max(check[1] for check in checks);max_field=max(check[2] for check in checks)
            elapsed=statistics.median(actual[3] for actual in runs)
            row=dict(mode=mode,passed=not problems,seconds=elapsed,max_raw_error=max_raw,
                     max_field_error=max_field,details=problems[:12])
            results.append(row)
            print('[WCT Strict Audit] mode={} {} median_region={:.4f}s raw_error={:.12g} field_error={:.12g} native_vertices={}'.format(
                mode,'PASS' if row['passed'] else 'FAIL',elapsed,max_raw,max_field,runs[-1][4]))
            if problems:print('[WCT Strict Audit Details]',problems[:12])
        except Exception as exc:
            results.append(dict(mode=mode,passed=False,error=repr(exc)))
            print('[WCT Strict Audit] mode={} ERROR {}'.format(mode,repr(exc)))
    legacy=next(row for row in results if row['mode']==0)
    passing=[row for row in results if row['passed']]
    if legacy['passed'] and passing:
        best=min(passing,key=lambda row:row['seconds'])
        if best['seconds']<python_seconds:selected=best['mode']
    if selected is not None:
        N.remember_audit(selected)
        print('[WCT Strict Audit] SELECTED mode={} for binary {}'.format(selected,identity['sha256'][:12]))
        print('[WCT Strict Audit] Preference saved; tool can reuse it after Maya restart with this same binary.')
    else:
        N.clear_audit()
        print('[WCT Strict Audit] Python retained; no faster parity-checked mode selected.')
finally:
    B.clear_cache();S._prepare_boundary_queries=original_prepare
    for key,value in previous.items():
        if value is None:os.environ.pop(key,None)
        else:os.environ[key]=value
print('[WCT Strict Audit END] No weights/curves/selection changed. Results are in variable results.')
