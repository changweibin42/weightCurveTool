# -*- coding: utf-8 -*-
"""Paste the whole file into Maya Script Editor's Python tab and execute.

Exports only the current mesh, three curves, and numeric solver inputs.
Does not edit skin weights, geometry, colours, selection, or tool settings.
A solver failure is captured in the JSON and does NOT prevent saving it.
"""
import hashlib
import importlib
import marshal
import json
import os
import sys
import traceback
import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om
from weightCurveTool.tools import StrictCurveRegion as region_module

MESH = 'cloth_01_geo_low_lbs'
DEFAULT_CURVES = (MESH+'_ring_laplacian', MESH+'_fwd_ring_laplacian', MESH+'_bwd_ring_laplacian')


def _xyz(p):
    return [float(p.x), float(p.y), float(p.z)]



def _field_component_audit(edges, fixed, count, required):
    """Reproduce the solver's guard classification without solving/writing."""
    fixed={int(v):float(q) for v,q in fixed.items()};required=set(map(int,required))
    unknown=set(range(count))-set(fixed)
    links={v:set() for v in unknown};boundary={v:[] for v in unknown}
    for a,b,length,cuts in edges:
        if cuts:
            if a in unknown:boundary[a].append((float(cuts[0][1]),int(a),int(b)))
            if b in unknown:boundary[b].append((float(cuts[-1][1]),int(a),int(b)))
        else:
            if a in unknown and b in fixed:boundary[a].append((fixed[b],int(a),int(b)))
            if b in unknown and a in fixed:boundary[b].append((fixed[a],int(a),int(b)))
            if a in unknown and b in unknown:links[a].add(b);links[b].add(a)
    remaining=set(unknown);problems=[];ignored=0
    while remaining:
        seed=min(remaining);remaining.remove(seed);queue=[seed];group=[];constraints=[]
        while queue:
            v=queue.pop();group.append(v);constraints.extend(boundary[v])
            for nb in links[v]:
                if nb in remaining:remaining.remove(nb);queue.append(nb)
        if not (set(group)&required):ignored+=1;continue
        values=[q for q,_,_ in constraints]
        reason=('no_constraints' if not values else
                'ridge_gap' if min(values)<.5-1e-8 and max(values)>.5+1e-8 else None)
        if reason:
            problems.append(dict(reason=reason,vertices=sorted(group),
                required_vertices=sorted(set(group)&required),
                boundary_values=sorted(set(values)),
                boundary_edges=[[q,a,b] for q,a,b in constraints]))
    return dict(problem_components=problems,ignored_auxiliary_components=ignored)


def _tool_runtime_details():
    details={}
    for name in ('StrictCurveRegion','WeightCalculator','ModifyCurveTool','DualCurveBuilder','WeightColorPipeline','NativeEdgeCuts','SurfaceBinder','CurveLocalPoints'):
        module=importlib.import_module('weightCurveTool.tools.'+name)
        path=getattr(module,'__file__',None);record=dict(path=path)
        if path and os.path.isfile(path):
            with open(path,'rb') as f:record['disk_sha256']=hashlib.sha256(f.read()).hexdigest()
        details[name]=record
    try:
        from weightCurveTool import core
        ctx=core._ctx
        details['context']=dict(active_set=ctx.group_mgr.active_set,
            active_groups={k:list(v) for k,v in ctx.group_mgr.active_groups.items()},
            mode=ctx.weight_calc.get_mode(),xs=list(ctx.last_xs),ys=list(ctx.last_ys),
            picked=ctx.reference_joint,parent=ctx.parent_joint,
            cached_region_count=len(getattr(ctx.weight_calc,'_surface_region_cache',{})))
        code=ctx.weight_calc._compute_boundary_mode_loft.__code__
        details['WeightCalculator']['loaded_method_sha256']=hashlib.sha256(marshal.dumps(code)).hexdigest()
    except Exception as exc:details['context_warning']=str(exc)
    details['backend_environment']={key:os.environ.get(key) for key in (
        'WCT_NATIVE_EDGE_CUTS','WCT_NATIVE_CURVE_CACHE','WCT_NATIVE_ACCEL_POINT',
        'WCT_NATIVE_ACCEL_NORMAL','WCT_NATIVE_BVH_POINT','WCT_NATIVE_FACE_QUERY')}
    try:
        details['plugin']=dict(path=cmds.pluginInfo('wctSurfaceCurve',q=True,path=True),
                               version=cmds.pluginInfo('wctSurfaceCurve',q=True,version=True))
    except Exception as exc:
        details['plugin_warning']=str(exc)
    for name in ('build_strict_region','weight_coordinates','_edge_cuts',
                 '_curve_surface_witnesses','_recover_witness_cuts','_solve_dirichlet'):
        fn=getattr(region_module,name,None)
        if fn is not None:
            details.setdefault('loaded_geometry_functions',{})[name]=hashlib.sha256(marshal.dumps(fn.__code__)).hexdigest()
    return details

def _make_payload(mesh_name, curve_names):
    mesh = om.MFnMesh(region_module._wct_shape(mesh_name, 'mesh'))
    points = mesh.getPoints(om.MSpace.kWorld)
    helper_path = getattr(region_module, '__file__', None)
    source_hash = None
    if helper_path and os.path.isfile(helper_path):
        with open(helper_path, 'rb') as stream:
            source_hash = hashlib.sha256(stream.read()).hexdigest()
    payload = dict(format='weightCurveTool-field-case-v3', mesh=mesh_name,
                   maya_version=str(cmds.about(version=True)), python_version=sys.version,
                   helper_path=helper_path, helper_sha256=source_hash,
                   vertices=[_xyz(p) for p in points],
                   polygons=[list(map(int, mesh.getPolygonVertices(i))) for i in range(mesh.numPolygons)],
                   curves=[], solver_error=None, export_warnings=[])
    try:
        payload['vertex_normals'] = [_xyz(n) for n in mesh.getVertexNormals(True, om.MSpace.kWorld)]
    except Exception as exc:
        payload['export_warnings'].append('Optional vertex normals: '+str(exc))
    try:
        payload['runtime']=_tool_runtime_details()
    except Exception:
        payload['export_warnings'].append('Runtime metadata: '+traceback.format_exc())
    curve_functions = []
    for name in curve_names:
        fn = om.MFnNurbsCurve(region_module._wct_shape(name, 'nurbsCurve'))
        curve_functions.append(fn)
        payload['curves'].append(dict(name=name, degree=int(fn.degree), form=int(fn.form),
            cvs=[_xyz(p)+[float(p.w)] for p in fn.cvPositions(om.MSpace.kWorld)],
            knots=[float(k) for k in fn.knots()]))
        record=payload['curves'][-1]
        for attr in ('wcs_localPoints','wcs_displayPoints'):
            try:
                record[attr]=cmds.getAttr(name+'.'+attr)
            except Exception:
                pass
    try:
        from weightCurveTool import core
        payload['loc_bindings']={}
        for kind in ('main','fwd','bwd'):
            rows=[]
            for joint in core._ctx.group_mgr.get_joints_from_active(kind):
                row=dict(name=joint,position=cmds.xform(joint,q=True,ws=True,t=True))
                for attr in ('wct_mesh','wct_id0','wct_id1','wct_id2','wct_u','wct_v','wct_w','wct_boundaryCorrespondence'):
                    if cmds.attributeQuery(attr,node=joint,exists=True):
                        row[attr]=cmds.getAttr(joint+'.'+attr)
                rows.append(row)
            payload['loc_bindings'][kind]=rows
    except Exception:
        payload['export_warnings'].append('Binding metadata: '+traceback.format_exc())
    region = None
    try:
        region = region_module.build_strict_region(mesh_name, curve_names)
        region_module.weight_coordinates(region, mesh_name)
    except Exception:
        payload['solver_error'] = traceback.format_exc()
        print('[CurveCaseExport] Solver result captured, continuing export:\n'+payload['solver_error'])
    if region is not None:
        payload.update(median=float(region['median']),
            selected=[int(v) for v in np.flatnonzero(region['mask'])],
            fwd_outer=[int(v) for v in np.flatnonzero(region['fwd_outer'])],
            solver_support='whole_mesh',
            edges=[[int(a), int(b), float(length), [[float(t),float(q)] for t,q in cuts]]
                   for a,b,length,cuts in region.get('field_edges',[])],
            fixed={str(int(v)):float(q) for v,q in region.get('field_fixed',{}).items()})
        payload['component_audit']=_field_component_audit(
            payload['edges'],payload['fixed'],len(points),payload['selected'])
        payload['edge_witnesses']=[[[int(a),int(b),[[float(u),int(i),int(j)] for u,i,j in hits]]
            for (a,b),hits in sorted(by_edge.items())]
            for by_edge in region.get('curve_edge_witnesses',[])]
        payload['witness_recoveries']=region.get('field_witness_recoveries',[])
        payload['fan_bridges']=region.get('field_fan_bridges',[])
        for record in payload['curves']:
            record['surface_samples']=[_xyz(p)+[int(face),float(param)]
                                       for p,face,param in region['curve_samples'][record['name']]]
        if 'decay_field' in region:
            payload['field']=[float(q) for q in region['decay_field']]
        # Actual native endpoint values help identify missing cuts without
        # requiring the original rig or guessing how Maya evaluated normals.
        eps=max(region['median']*region_module.STRICT_EPS_FACTOR,1e-10)
        for record,fn in zip(payload['curves'],curve_functions):
            try:
                near,endpoint,evaluate=region_module._curve_edge_data(region,mesh,fn,record['name'],eps)
                record['native_endpoint_tests']={}
                for v in sorted(near):
                    result=endpoint(v)
                    record['native_endpoint_tests'][str(int(v))] = (
                        None if result is None else [float(result[0]),float(result[1])])
            except Exception:
                payload['export_warnings'].append('Endpoint tests '+record['name']+': '+traceback.format_exc())
    # Fail before touching the destination if any unexpected native type or
    # non-finite number escaped conversion.
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return payload


def export_curve_field_case():
    paths=cmds.fileDialog2(fileMode=0, fileFilter='JSON (*.json)',
                          caption='Save curve field numeric case')
    if not paths:
        print('[CurveCaseExport] Cancelled; nothing changed')
        return None
    path=paths[0]
    if not path.lower().endswith('.json'):
        path += '.json'
    curve_names=DEFAULT_CURVES
    from weightCurveTool import core
    from weightCurveTool.tools.WeightColorPipeline import _active_boundary_curve_names
    active=_active_boundary_curve_names(core._ctx)
    if active is not None:
        if not all(MESH in name.split('|')[-1] for name in active):
            raise RuntimeError('当前活动曲线不是报告中的模型，请保持出错模型/曲线再导出')
        curve_names=active
    elif getattr(core._ctx.group_mgr,'active_set',None):
        raise RuntimeError('当前活动 set 不完整；不能回退到其他同名曲线导出')
    print('[CurveCaseExport] Reading mesh and curves. Skin weights will not be written.')
    # Run the exact original Python field path for this failure export.
    # No native or incremental-cache result can mask the missing constraints.
    keys=('WCT_NATIVE_EDGE_CUTS','WCT_NATIVE_CURVE_CACHE')
    saved={key:os.environ.get(key) for key in keys}
    try:
        for key in keys:os.environ[key]='0'
        payload=_make_payload(MESH,tuple(curve_names))
        payload['export_solver_backend']='python_uncached'
        payload['original_backend_environment']=saved
    finally:
        for key,value in saved.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
    temporary=path+'.tmp'
    try:
        with open(temporary,'w',encoding='utf-8') as stream:
            json.dump(payload,stream,ensure_ascii=False,allow_nan=False)
        os.replace(temporary,path)
    except Exception:
        if os.path.isfile(temporary):os.remove(temporary)
        raise
    print('[CurveCaseExport] SAVED: '+path)
    print('[CurveCaseExport] vertices={}, selected={}, constraints={}, solver_failed={}'.format(
        len(payload['vertices']),len(payload.get('selected',[])),len(payload.get('edges',[])),
        bool(payload['solver_error'])))
    print('[CurveCaseExport] problem_components={}'.format(
        len(payload.get('component_audit',{}).get('problem_components',[]))))
    print('[CurveCaseExport] Upload this JSON for reproduction. Skin weights were unchanged.')
    return path


WCT_EXPORTED_CASE = export_curve_field_case()
