# -*- coding: utf-8 -*-
"""Export actual Maya closest-point discrepancies; never write skin/curves.
Requires the existing 1.5.0 plugin and current complete curve set.
Also exports an invalid snapped-loop case if strict region construction fails.
"""
import json
import math
import os
import traceback
from pathlib import Path
from time import perf_counter
import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om
from weightCurveTool import core
from weightCurveTool.tools import StrictCurveRegion as S
from weightCurveTool.tools.WeightColorPipeline import _active_boundary_curve_names

KEYS=('WCT_NATIVE_EDGE_CUTS','WCT_NATIVE_ACCEL_POINT','WCT_NATIVE_ACCEL_NORMAL',
      'WCT_NATIVE_BVH_POINT','WCT_NATIVE_FACE_QUERY')


def xyz(point):
    return [float(point.x),float(point.y),float(point.z)]


def matrix_rows(matrix):
    return [[float(matrix.getElement(i,j)) for j in range(4)] for i in range(4)]


def curve_record(name):
    dag=S._wct_shape(name,'nurbsCurve');fn=om.MFnNurbsCurve(dag)
    result=dict(name=name,degree=fn.degree,form=int(fn.form),
                cvs_object=[[float(p.x),float(p.y),float(p.z),float(p.w)]
                            for p in fn.cvPositions(om.MSpace.kObject)],
                cvs_world=[[float(p.x),float(p.y),float(p.z),float(p.w)]
                           for p in fn.cvPositions(om.MSpace.kWorld)],
                knots=list(map(float,fn.knots())),matrix=matrix_rows(dag.inclusiveMatrix()))
    # Preserve saved authoritative coordinates as well as actual displayed geometry.
    for attr in ('wcs_localPoints','wcs_displayPoints'):
        try:
            result[attr]=cmds.getAttr(name+'.'+attr)
        except Exception:
            pass
    return result


class FaceQuery:
    def __init__(self,mesh,face,object_points,keep_global_ids=False):
        ids=list(map(int,mesh.getPolygonVertices(face)))
        self.ids=ids
        points=object_points if keep_global_ids else [object_points[v] for v in ids]
        connects=ids if keep_global_ids else list(range(len(ids)))
        self.owner=om.MFnMeshData().create()
        self.fn=om.MFnMesh()
        self.fn.create(om.MPointArray(points),[len(ids)],connects,parent=self.owner)
        actual=self.fn.getPoints(om.MSpace.kObject)
        if keep_global_ids and len(actual)!=len(object_points):
            self.vertex_error=None
        else:
            wanted=object_points if keep_global_ids else [object_points[v] for v in ids]
            self.vertex_error=max((actual[i]-wanted[i]).length() for i in range(len(wanted)))

    def query(self,point):
        return self.fn.getClosestPoint(point,om.MSpace.kObject)[0]


def main():
    ctx=core._ctx
    mesh_name=ctx.continuous_mesh or ctx.group_mgr.find_mesh_from_active()
    names=_active_boundary_curve_names(ctx)
    if not mesh_name or not names or len(names)!=3:
        raise RuntimeError('请先打开工具并激活完整的 main/FWD/BWD 曲线组')
    paths=cmds.fileDialog2(fileMode=0,caption='保存权重工具最近点差异现场',
                         fileFilter='JSON (*.json)',startingDirectory=os.path.expanduser('~'))
    if not paths:
        print('[WCT Query Export] cancelled; scene unchanged')
        return
    output=Path(paths[0])
    if output.suffix.lower()!='.json':
        output=output.with_suffix('.json')
    started=perf_counter()
    dag=S._wct_shape(mesh_name,'mesh');mesh=om.MFnMesh(dag)
    world=dag.inclusiveMatrix();inverse=dag.inclusiveMatrixInverse()
    object_points=mesh.getPoints(om.MSpace.kObject)
    world_points=mesh.getPoints(om.MSpace.kWorld)
    counts,connects=mesh.getVertices();tri_counts,tri_vertices=mesh.getTriangles()
    result=dict(format='wct_closest_query_case_v1',mesh=mesh_name,
                plugin_path=cmds.pluginInfo('wctSurfaceCurve',q=True,path=True),
                plugin_version=cmds.pluginInfo('wctSurfaceCurve',q=True,version=True),
                mesh_matrix=matrix_rows(world),vertices_object=[xyz(p) for p in object_points],
                vertices_world=[xyz(p) for p in world_points],
                polygon_counts=list(map(int,counts)),polygon_vertices=list(map(int,connects)),
                triangle_counts=list(map(int,tri_counts)),triangle_vertices=list(map(int,tri_vertices)),
                curves=[curve_record(name) for name in names],query_records=[])
    old={key:os.environ.get(key) for key in KEYS}
    try:
        region=S.build_strict_region(mesh_name,tuple(names))
        result['median']=float(region['median'])
        result['strict_mask']=np.asarray(region['mask'],dtype=bool).tolist()
        result['fwd_outer_mask']=np.asarray(region['fwd_outer'],dtype=bool).tolist()
        result['surface_samples']={name:[[float(p.x),float(p.y),float(p.z),int(face),float(param)]
                                 for p,face,param in region['curve_samples'][name]] for name in names}
        os.environ['WCT_NATIVE_EDGE_CUTS']='0'
        S.weight_coordinates(region,mesh_name)
        baseline=[(int(a),int(b),float(length),list(cuts)) for a,b,length,cuts in region['field_edges']]
        result['python_edges']=baseline
        result['python_field']=np.asarray(region['decay_field']).tolist()
        os.environ.update(WCT_NATIVE_EDGE_CUTS='1',WCT_NATIVE_ACCEL_POINT='0',
                          WCT_NATIVE_ACCEL_NORMAL='1',WCT_NATIVE_BVH_POINT='0',WCT_NATIVE_FACE_QUERY='1')
        S.weight_coordinates(region,mesh_name)
        if not region.get('field_native_backend_used'):
            raise RuntimeError('候选面命令未生效；已保存其余输入现场')
        native=[(int(a),int(b),float(length),list(cuts)) for a,b,length,cuts in region['field_edges']]
        result['candidate_edges']=native
        result['candidate_field']=np.asarray(region['decay_field']).tolist()
        eps=max(region['median']*S.STRICT_EPS_FACTOR,1e-10)
        result['eps']=float(eps)
        mismatches=[]
        for expected,actual in zip(baseline,native):
            a,b,length,cuts=expected
            if (a,b)!=actual[:2] or len(cuts)!=len(actual[3]):
                mismatches.append(dict(edge=[a,b],python=cuts,native=actual[3],reason='structure'))
                continue
            for (u,value),(v,other) in zip(cuts,actual[3]):
                if abs(u-v)*length>eps*4 or abs(value-other)>1e-12:
                    mismatches.append(dict(edge=[a,b],python_u=float(u),native_u=float(v),
                        value=float(value),world_error=float(abs(u-v)*length)))
        result['mismatches']=mismatches
        functions=[om.MFnNurbsCurve(S._wct_shape(name,'nurbsCurve')) for name in names]
        local_faces={};full_faces={}
        # Inspect current mismatches automatically; no hard-coded vertex IDs.
        for issue in mismatches[:24]:
            if 'python_u' not in issue:
                continue
            a,b=issue['edge'];ci=min(range(3),key=lambda i:abs((.5,1.,0.)[i]-issue['value']))
            curve=functions[ci]
            for u in (issue['python_u'],issue['native_u'],(issue['python_u']+issue['native_u'])*.5):
                edge_point=world_points[a]+(world_points[b]-world_points[a])*u
                curve_point,param=curve.closestPoint(edge_point,tolerance=eps*.1,space=om.MSpace.kWorld)
                legacy,face=mesh.getClosestPoint(curve_point,om.MSpace.kWorld)
                object_query=curve_point*inverse
                original_object,object_face=mesh.getClosestPoint(object_query,om.MSpace.kObject)
                transformed=original_object*world
                rec=dict(edge=[a,b],u=float(u),curve_index=ci,curve_parameter=float(param),
                         edge_point=xyz(edge_point),curve_point=xyz(curve_point),object_query=xyz(object_query),
                         legacy_world=xyz(legacy),legacy_face=int(face),
                         legacy_object=xyz(original_object),legacy_object_face=int(object_face),
                         object_world_difference=float((transformed-legacy).length()),
                         face_vertex_ids=list(map(int,mesh.getPolygonVertices(face))))
                for label,cache,global_ids in (('local_ids',local_faces,False),('global_ids',full_faces,True)):
                    try:
                        if face not in cache:
                            cache[face]=FaceQuery(mesh,face,object_points,global_ids)
                        single=cache[face].query(object_query)
                        rec[label]=dict(point_object=xyz(single),point_world=xyz(single*world),
                                        difference=float((single*world-legacy).length()),
                                        recreated_vertex_error=cache[face].vertex_error)
                    except Exception as exc:
                        rec[label]=dict(error=repr(exc))
                result['query_records'].append(rec)
        print('[WCT Query Export] mismatches={} inspected_queries={}'.format(len(mismatches),len(result['query_records'])))
        for label in ('local_ids','global_ids'):
            errors=[rec[label]['difference'] for rec in result['query_records'] if 'difference' in rec.get(label,{})]
            if errors:print('[WCT Query Export] {} max_projection_difference={:.12g}'.format(label,max(errors)))
    except Exception:
        result['error']=traceback.format_exc()
        print('[WCT Query Export] calculation error captured; geometry will still be exported')
        print(result['error'])
    finally:
        for key,value in old.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
    result['export_seconds']=perf_counter()-started
    with output.open('w',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    print('[WCT Query Export] saved:',str(output))
    print('[WCT Query Export] no scene weights/curves/selection changed; backend settings restored')


if __name__=='__main__':
    main()
