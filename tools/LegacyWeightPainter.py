# -*- coding: utf-8 -*-
"""LegacyWeightPainter —— bezierWeights 遗留算法（OpenMaya 2.0 重写）

7 种权重模式：
  · soft   次级（Maya 软选择 falloff → UI 拾取的目标骨骼）
  · ik     关节（UI pick + parent，自动识别有符号链轴）
  · spine  脊椎（用户解锁的唯一单链）
  · brow   眉毛（split vp=1，重排 joint 成链）
  · belt   背带（split vp=2）
  · cloth  裙子（主链间自然衰减 + 可选链内 FK）
  · eye    眼皮（BFS 分配顶点）

关键语义：
  · soft 模式写入目标 = target_joint（ctx.reference_joint，UI 拾取骨骼）
  · soft/ik/spine 仅写指定 influence 与范围，保留原可分配总量
  · cloth 模式 source = target_joint（独立暂存骨骼，不在任何主链 DAG 里）
  · 其他模式沿用旧语义，写入 paintTrans 或全部解锁 joint
  · soft sources/spine liw 由用户设置；soft pick/IK 父子按需临时解锁。

接口约定：
  xs, ys: 4 个控制点，ys 已翻转为"Y 向下"（与旧版一致）
  r: 半径倍数
"""
import re

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import numpy as np

from ..Logger import log


from .ProjectionRegions import (
    _projection_ownership_mask,
    _projection_connected_band,
    _projection_plane_rings,
    _projection_surface_distances,
    _projection_ik_ring_gate,
    _projection_axis_enclosed,
    _projection_boundary_loops,
    _projection_distal_seeds,
    _projection_surface_partition,
    _projection_complete_support_tails,
    _projection_ik_assignment_plan,
    _projection_spine_connected_influences,
)


from .ProjectionProfiles import (
    bezier_v,
    bezier_t,
    get_weight,
    _ik_x,
    _ik_xs,
    _ik_weights,
    _soft_weights,
    _split_weights,
    _cloth_weights,
    _cloth_point_to_chain_dist_t,
    _cloth_sample_chain_positions,
    _cloth_build_local_radius_curves,
    _cloth_evaluate_local_radius,
    _cloth_bezier_eval,
    _cloth_calc_main_chain_mix,
    _cloth_fk_chain_weights,
)


# ============================================================================
# Bezier 数学
# ============================================================================


# ============================================================================
# 纯数学
# ============================================================================


# ============================================================================
# 场景查询
# ============================================================================

def _resolve_mesh(mesh_name=None):
    if mesh_name is None:
        sel = cmds.ls(sl=True, o=True) or []
        if not sel:
            return None
        mesh_name = sel[0]
    if not cmds.objExists(mesh_name):
        return None
    if cmds.nodeType(mesh_name) == 'mesh':
        pars = cmds.listRelatives(mesh_name, parent=True) or []
        return pars[0] if pars else None
    shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh') or []
    return mesh_name if shapes else None


def _get_shape_name(mesh_name):
    shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh') or []
    return shapes[0] if shapes else None


def _find_skin_cluster(mesh_name):
    history = cmds.listHistory(mesh_name, pruneDagObjects=True) or []
    skins = cmds.ls(history, type='skinCluster') or []
    return skins[0] if skins else None


def _get_influences(skin_name):
    return cmds.skinCluster(skin_name, q=True, influence=True) or []


def _is_influence_locked(jnt):
    if not cmds.attributeQuery('liw', node=jnt, exists=True):
        return False
    try:
        return bool(cmds.getAttr(jnt + '.liw'))
    except Exception:
        return True


def _get_unlocked_influences(skin_name):
    return [j for j in _get_influences(skin_name) if not _is_influence_locked(j)]


def _get_paint_trans(skin_name):
    conns = cmds.listConnections(skin_name + '.paintTrans', s=True, d=False) or []
    joints = [c for c in conns if cmds.nodeType(c) == 'joint']
    return joints[0] if len(joints) == 1 else None


def _world_pos(node):
    return np.array(cmds.xform(node, q=True, ws=True, t=True), dtype=float)


def _world_x_axis(node):
    m = cmds.xform(node, q=True, ws=True, m=True)
    return np.array([m[0], m[1], m[2]], dtype=float)


def _mesh_world_points(shape_name):
    sel = om.MSelectionList()
    sel.add(shape_name)
    fn = om.MFnMesh(sel.getDagPath(0))
    pts = fn.getPoints(om.MSpace.kWorld)
    arr = np.empty((len(pts), 3), dtype=float)
    for i, p in enumerate(pts):
        arr[i, 0], arr[i, 1], arr[i, 2] = p.x, p.y, p.z
    return arr


def _mesh_fn(shape_name):
    sel = om.MSelectionList()
    sel.add(shape_name)
    return om.MFnMesh(sel.getDagPath(0))


def _build_fn_skin(skin_name):
    sel = om.MSelectionList()
    sel.add(skin_name)
    return oma.MFnSkinCluster(sel.getDependNode(0))


def _shape_dag(mesh_name):
    shape = _get_shape_name(mesh_name)
    sel = om.MSelectionList()
    sel.add(shape)
    return sel.getDagPath(0)


def _read_weights(fn_skin, path, indices, vtx_count):
    comp_fn = om.MFnSingleIndexedComponent()
    comp = comp_fn.create(om.MFn.kMeshVertComponent)
    comp_fn.addElements(list(range(int(vtx_count))))
    if not isinstance(indices, om.MIntArray):
        indices = om.MIntArray(indices)
    values = fn_skin.getWeights(path, comp, indices)
    return np.asarray(list(values), dtype=float).reshape(
        int(vtx_count), len(indices))


def _write_weights(fn_skin, path, indices, weights_matrix):
    comp_fn = om.MFnSingleIndexedComponent()
    comp = comp_fn.create(om.MFn.kMeshVertComponent)
    comp_fn.addElements(list(range(weights_matrix.shape[0])))
    flat = weights_matrix.reshape(-1).tolist()
    fn_skin.setWeights(path, comp, indices, om.MDoubleArray(flat),
                       normalize=False)


def _skin_mesh(skin_name):
    shapes = cmds.skinCluster(skin_name, q=True, geometry=True) or []
    if not shapes:
        return None
    pars = cmds.listRelatives(shapes[0], parent=True) or []
    return pars[0] if pars else shapes[0]


def _update_paint(skin_name):
    paint_jnt = _get_paint_trans(skin_name)
    if not paint_jnt:
        return
    if not cmds.attributeQuery('ptw', node=skin_name, exists=True):
        return
    influences = _get_influences(skin_name)
    if paint_jnt not in influences:
        return
    index = influences.index(paint_jnt)
    mesh = _skin_mesh(skin_name)
    if not mesh:
        return
    try:
        fn_skin = _build_fn_skin(skin_name)
        path = _shape_dag(mesh)
        n = int(cmds.polyEvaluate(mesh, vertex=True))
        weights = _read_weights(fn_skin, path, [index], n)[:, 0]
        cmds.setAttr(skin_name + '.ptw', weights.tolist(), type='doubleArray')
    except Exception as e:
        log.warn(f"更新 paint 显示失败: {e}")


def _set_mesh_points(shape_name, points_arr):
    if points_arr is None:
        return
    fn = _mesh_fn(shape_name)
    mpts = om.MPointArray()
    for p in points_arr:
        mpts.append(om.MPoint(float(p[0]), float(p[1]), float(p[2])))
    fn.setPoints(mpts, om.MSpace.kWorld)


def _short_name(node):
    if not node:
        return ""
    return node.split('|')[-1].split(':')[-1]


def _find_parent_influence(joint, skin_name):
    influences = _get_influences(skin_name)
    if not influences:
        return None
    full_set = set(influences)
    short_to_full = {_short_name(j): j for j in influences}

    cur = joint
    for _ in range(64):
        pars = cmds.listRelatives(cur, parent=True, fullPath=True) or []
        if not pars:
            return None
        par = pars[0]
        if par in full_set:
            return par
        short_par = _short_name(par)
        if short_par in short_to_full:
            return short_to_full[short_par]
        cur = par
    return None


# ============================================================================
# 模式：soft（次级）
# ============================================================================

def _projection_influence(node, influences):
    """Resolve full DAG identity; do not strip namespaces or guess duplicates."""
    if not node:
        return None
    matches=cmds.ls(node,long=True) or []
    if len(matches)!=1:
        return None
    resolved=matches[0]
    found=[j for j in influences if (cmds.ls(j,long=True) or [])==[resolved]]
    return found[0] if len(found)==1 else None


def _projection_state(mesh_name,skin_name):
    fn=_build_fn_skin(skin_name);path=_shape_dag(mesh_name)
    influences=[p.fullPathName() for p in fn.influenceObjects()]
    count=int(cmds.polyEvaluate(mesh_name,vertex=True))
    old=_read_weights(fn,path,list(range(len(influences))),count)
    if not np.all(np.isfinite(old)) or np.any(old < -1e-10):
        raise RuntimeError('Invalid existing skin weights')
    return fn,path,influences,old


def _projection_write(fn,path,old,indices,values,vertex_ids=None):
    """Write only explicit vertices/columns, preserving the group's budget."""
    ids=np.arange(len(old),dtype=int) if vertex_ids is None else np.asarray(vertex_ids,dtype=int)
    columns=np.asarray(indices,dtype=int);values=np.asarray(values,dtype=float)
    if not len(ids):return False
    if (len(set(ids.tolist()))!=len(ids) or np.any(ids<0) or np.any(ids>=len(old))
            or len(set(columns.tolist()))!=len(columns) or np.any(columns<0)
            or np.any(columns>=old.shape[1]) or values.shape!=(len(ids),len(columns))
            or not np.all(np.isfinite(values)) or np.any(values < -1e-10)):
        raise RuntimeError('Invalid projection weight write')
    before=old[np.ix_(ids,columns)]
    if not np.allclose(values.sum(axis=1),before.sum(axis=1),atol=1e-8,rtol=0):
        raise RuntimeError('Projection operation changed its influence budget')
    comp_fn=om.MFnSingleIndexedComponent()
    comp=comp_fn.create(om.MFn.kMeshVertComponent);comp_fn.addElements(ids.tolist())
    try:
        fn.setWeights(path,comp,om.MIntArray(columns.tolist()),
                      om.MDoubleArray(values.reshape(-1).tolist()),normalize=False)
        actual=_read_weights(fn,path,list(range(old.shape[1])),len(old))
        expected=old.copy();expected[np.ix_(ids,columns)]=values
        if not np.allclose(actual,expected,atol=1e-6,rtol=0):
            raise RuntimeError('Skin readback differs from requested projection weights')
    except Exception:
        # Restore the complete matrix: a Maya setting may have modified a
        # column outside the requested group, so restoring only it is unsafe.
        try:_write_weights(fn,path,om.MIntArray(list(range(old.shape[1]))),old)
        except Exception as exc:
            raise RuntimeError('Projection rollback failed; automatic retry cancelled: {}'.format(exc)) from exc
        raise
    return True


def _projection_redistribute(current,fraction):
    """Column 0 is picked; source columns retain their original proportions."""
    current=np.asarray(current,dtype=float);fraction=np.clip(np.asarray(fraction,dtype=float),0.,1.)
    cap=current.sum(axis=1);target=cap*fraction
    source=current[:,1:];total=source.sum(axis=1)
    # A picked-only row has no source ratio. Use a deterministic source
    # (parent if the caller ordered it first) to receive released weight.
    ratios=np.divide(source,total[:,None],out=np.zeros_like(source),where=total[:,None]>1e-12)
    ratios[total<=1e-12,0]=1.
    return np.column_stack([target,ratios*(cap-target)[:,None]])


def _projection_soft_selection(mesh_name,count):
    """Read current rich selection, including symmetry. Never move geometry."""
    shape=_shape_dag(mesh_name).fullPathName()
    rich=om.MGlobal.getRichSelection()
    falloff=np.zeros(count,dtype=float)
    for selection in (rich.getSelection(),rich.getSymmetry()):
        for i in range(selection.length()):
            try:dag,component=selection.getComponent(i)
            except RuntimeError:continue
            if dag.fullPathName()!=shape or not component.hasFn(om.MFn.kMeshVertComponent):
                continue
            fn=om.MFnSingleIndexedComponent(component)
            elements=list(range(count)) if fn.isComplete else list(fn.getElements())
            for k,v in enumerate(elements):
                weight=fn.weight(k).influence if fn.hasWeights else 1.
                if not np.isfinite(weight):raise RuntimeError('Non-finite soft selection weight')
                falloff[v]=max(falloff[v],float(np.clip(weight,0.,1.)))
    return falloff


def _projection_transform_points(points,matrix):
    points=np.asarray(points,dtype=float);matrix=np.asarray(matrix,dtype=float).reshape(4,4)
    homogeneous=np.column_stack((points,np.ones(len(points))))@matrix
    if not np.all(np.isfinite(homogeneous)) or np.any(abs(homogeneous[:,3])<1e-12):
        raise RuntimeError('Invalid bind geometry transform')
    return homogeneous[:,:3]/homogeneous[:,3,None]


def _projection_bind_reference(mesh_name,skin_name):
    """Read skin input + bind transforms, never restore or pose scene nodes."""
    fn=_build_fn_skin(skin_name);path=_shape_dag(mesh_name)
    geometry_index=int(fn.indexForOutputShape(path.node()))
    input_element=fn.findPlug('input',False).elementByLogicalIndex(geometry_index)
    data=input_element.child(fn.attribute('inputGeometry')).asMObject()
    if data.isNull():raise RuntimeError('Skin input geometry is unavailable')
    mesh=om.MFnMesh(data)
    current=_mesh_fn(_get_shape_name(mesh_name))
    source_sizes,source_vertices=mesh.getVertices();live_sizes,live_vertices=current.getVertices()
    if (int(mesh.numVertices)!=int(current.numVertices) or list(source_sizes)!=list(live_sizes)
            or list(source_vertices)!=list(live_vertices)):
        raise RuntimeError('Bind input/current output topology differs; vertex-ID mapping is unsafe')
    obj=mesh.getPoints(om.MSpace.kObject)
    local=np.array([[p.x,p.y,p.z] for p in obj],dtype=float)
    geom=np.asarray(cmds.getAttr(skin_name+'.geomMatrix'),dtype=float).reshape(4,4)
    points=_projection_transform_points(local,geom)
    matrices={};positions={};logical_indices={}
    for influence in fn.influenceObjects():
        name=influence.fullPathName();logical=int(fn.indexForInfluenceObject(influence))
        pre=np.asarray(cmds.getAttr('{}.bindPreMatrix[{}]'.format(skin_name,logical)),dtype=float).reshape(4,4)
        try:matrix=np.linalg.inv(pre)
        except np.linalg.LinAlgError:raise RuntimeError('Singular bindPreMatrix: '+name)
        if not np.all(np.isfinite(matrix)):raise RuntimeError('Invalid bind transform: '+name)
        matrices[name]=matrix;positions[name]=matrix[3,:3].copy();logical_indices[name]=logical
    return dict(space='bind',points=points,matrices=matrices,positions=positions,
                geometry_index=geometry_index,logical_indices=logical_indices)


def _projection_reference(mesh_name,skin_name,space):
    if space=='current':return None
    if space!='bind':raise RuntimeError('Unknown reference space: '+str(space))
    reference=_projection_bind_reference(mesh_name,skin_name)
    log.info('[Projection Bind V17.5.0] vertices={}, geometry_index={}'.format(
        len(reference['points']),reference['geometry_index']))
    return reference


def _projection_reference_position(node,reference=None):
    if reference is None:return _world_pos(node)
    key=node if node in reference['positions'] else _projection_influence(node,list(reference['positions']))
    if key is None:raise RuntimeError('No bind transform for '+str(node))
    return np.asarray(reference['positions'][key],dtype=float)


def _projection_reference_matrix(node,reference=None):
    if reference is None:return np.asarray(cmds.xform(node,q=True,ws=True,m=True),dtype=float).reshape(4,4)
    key=node if node in reference['matrices'] else _projection_influence(node,list(reference['matrices']))
    if key is None:raise RuntimeError('No bind matrix for '+str(node))
    return np.asarray(reference['matrices'][key],dtype=float).reshape(4,4)


def _projection_reference_points(shape,reference=None):
    return _mesh_world_points(shape) if reference is None else np.asarray(reference['points'],dtype=float)


def _projection_soft_radius():
    value=cmds.softSelect(q=True,softSelectDistance=True)
    radius=1. if value is None else float(value)
    if not np.isfinite(radius) or radius<=0:raise RuntimeError('Maya 软选择半径必须大于零')
    return radius


def _projection_aim_axis(joint,aim,reference=None):
    """Infer a signed primary matrix axis from the actual chain segment."""
    aim=np.asarray(aim,dtype=float)
    length=float(np.linalg.norm(aim))
    if not np.isfinite(length) or length<1e-10:
        raise RuntimeError('Coincident joints cannot define the chain direction')
    aim=aim/length
    matrix=_projection_reference_matrix(joint,reference)
    rows=matrix[:3,:3];lens=np.linalg.norm(rows,axis=1)
    if not np.all(np.isfinite(rows)) or np.any(lens<1e-10):
        raise RuntimeError('Degenerate joint world matrix: '+joint)
    axes=rows/lens[:,None];dots=axes@aim
    ranking=np.argsort(np.abs(dots));i=int(ranking[-1])
    # Some rigs have diagonal child translations, so no single local primary
    # axis can be inferred reliably. Use the measured segment in that case.
    if abs(dots[i])<.90 or abs(dots[i])-abs(dots[ranking[-2]])<1e-4:
        return aim,'segment'
    return axes[i]*(1. if dots[i]>=0 else -1.),'XYZ'[i]+('+' if dots[i]>=0 else '-')


def _projection_vp(parent,child,next_joint=None,reference=None):
    incoming=_projection_reference_position(child,reference)-_projection_reference_position(parent,reference)
    if next_joint is None:
        children=cmds.listRelatives(child,children=True,type='joint',fullPath=True) or []
        # At a branch do not silently choose one outgoing limb.
        next_joint=children[0] if len(children)==1 else None
        if next_joint is not None and reference is not None:
            next_joint=_projection_influence(next_joint,list(reference['matrices']))
    outgoing=(_projection_reference_position(next_joint,reference)-_projection_reference_position(child,reference)) if next_joint else incoming
    if np.linalg.norm(outgoing)<1e-10:outgoing=incoming
    parent_axis,parent_label=_projection_aim_axis(parent,incoming,reference)
    child_axis,child_label=_projection_aim_axis(child,outgoing,reference)
    v=(parent_axis+child_axis)*.5
    if np.dot(v,v)<1e-12:
        raise RuntimeError('Chain doubles back at {}; cannot define a unique transition plane'.format(child))
    log.info('[Projection Axis] {}={} {}={}'.format(parent,parent_label,child,child_label))
    return v.tolist(),_projection_reference_position(child,reference).tolist()


def _projection_write_available(fn,path,old,indices,values,editable_joints,vertex_ids=None):
    """Try native writing first; temporarily unlock only if that fails.

    Sources for soft were already filtered BEFORE this call. Only soft pick,
    or the explicit IK pair, can be unlocked. Spine never uses this wrapper.
    """
    try:
        return _projection_write(fn,path,old,indices,values,vertex_ids)
    except Exception as first_error:
        if 'Projection rollback failed' in str(first_error):raise
        locked=[j for j in editable_joints if _is_influence_locked(j)]
        if not locked:raise
        changed=[]
        try:
            for j in locked:
                cmds.setAttr(j+'.liw',0);changed.append(j)
                if _is_influence_locked(j):raise RuntimeError('Cannot unlock '+j)
            log.info('[Projection liw] temporary unlock: {}'.format(changed))
            return _projection_write(fn,path,old,indices,values,vertex_ids)
        finally:
            for j in reversed(changed):
                cmds.setAttr(j+'.liw',1)


def _projection_single_chain(influences):
    unlocked=[j for j in influences if cmds.nodeType(j)=='joint' and not _is_influence_locked(j)]
    if len(unlocked)<2:
        raise RuntimeError('脊椎模式需要至少两个用户手动解锁的蒙皮 joint')
    active=set(unlocked);parents={};children={j:[] for j in unlocked}
    all_set=set(influences)
    for j in unlocked:
        cur=j;visited=set()
        for _ in range(256):
            candidates=cmds.listRelatives(cur,parent=True,fullPath=True) or []
            if not candidates:break
            cur=candidates[0]
            if cur in visited:raise RuntimeError('Cyclic joint DAG')
            visited.add(cur)
            influence=_projection_influence(cur,influences)
            if influence in all_set:
                if influence in active:parents[j]=influence;children[influence].append(j)
                break  # A locked influence cannot be skipped to join two chains.
        else:raise RuntimeError('Joint hierarchy exceeds supported depth')
    roots=[j for j in unlocked if j not in parents]
    if len(roots)!=1 or any(len(v)>1 for v in children.values()):
        raise RuntimeError('脊椎未锁骨骼必须组成一条无分叉单链；请手动检查 liw')
    chain=[];cur=roots[0]
    while cur:
        chain.append(cur);cur=children[cur][0] if children[cur] else None
    if len(chain)!=len(unlocked):raise RuntimeError('Unlocked spine joints are disconnected')
    return chain


def _projection_spine_weights(wx_matrix,budget,xs,ys,radius):
    """Reference split_weights: clamp each cumulative layer to its predecessor."""
    previous=np.ones(len(budget));columns=[]
    for wx in wx_matrix:
        layer=np.minimum(previous,np.clip(_ik_weights(wx,xs,ys,radius),0.,1.))
        columns.append(previous-layer);previous=layer
    columns.append(previous)
    return np.column_stack(columns)*np.asarray(budget)[:,None]


def _paint_soft(mesh_name,skin_name,xs,ys,r,target_joint=None):
    fn,path,influences,old=_projection_state(mesh_name,skin_name)
    picked=_projection_influence(target_joint,influences)
    if picked is None:
        log.warn('次级模式：请在 UI 拾取当前模型的蒙皮骨骼');return False
    sources=[j for j in influences if j!=picked and cmds.nodeType(j)=='joint'
             and not _is_influence_locked(j)]
    if not sources:
        log.warn('次级模式：至少需要一个用户已解锁的 source，工具不会自动解锁');return False
    falloff=_projection_soft_selection(mesh_name,len(old));ids=np.flatnonzero(falloff>0.)
    if not len(ids):
        log.warn('次级模式：请在当前模型选择顶点；范围取当前 Maya 软选择');return False
    # r changes the mapping within the existing rich-selection mask only.
    fraction=np.array(_soft_weights(((1.-falloff[ids])/r).tolist(),xs,ys,1.))
    parent=_projection_parent(picked,influences)
    if parent in sources:sources=[parent]+[j for j in sources if j!=parent]
    indices=[influences.index(j) for j in [picked]+sources]
    values=_projection_redistribute(old[np.ix_(ids,indices)],fraction)
    ok=_projection_write_available(fn,path,old,indices,values,[picked],ids)
    if ok:_update_paint(skin_name)
    log.info('[Projection V17.4.4 soft] pick={}, sources={}, vertices={}'.format(picked,sources,len(ids)))
    return bool(ok)


def _projection_parent(picked,influences):
    cur=picked
    for _ in range(256):
        pars=cmds.listRelatives(cur,parent=True,fullPath=True) or []
        if not pars:return None
        cur=pars[0];found=_projection_influence(cur,influences)
        if found is not None and cmds.nodeType(found)=='joint':return found
    raise RuntimeError('Cannot resolve parent influence')


def _projection_region_mask(mesh_name,influences,indices,points,mode='auto'):
    if mode=='selection':
        mask=_projection_soft_selection(mesh_name,len(points))>0.
    elif mode=='auto':
        positions=np.array([_world_pos(j) for j in influences])
        segments=[]
        for i,j in enumerate(influences):
            parent=_projection_parent(j,influences)
            if parent is not None:
                owner=influences.index(parent)
                segments.append((owner,owner,i))
        mask=_projection_ownership_mask(points,positions,segments,indices)
    else:
        raise RuntimeError('Unknown projection range mode: '+str(mode))
    log.info('[Projection Range] mode={}, accepted={}/{}'.format(mode,int(mask.sum()),len(mask)))
    return mask


def _projection_mesh_graph(mesh_name):
    mesh=_mesh_fn(_get_shape_name(mesh_name))
    sizes,vertices=mesh.getVertices()
    graph=[set() for _ in range(int(mesh.numVertices))]
    offset=0
    for size in sizes:
        polygon=list(vertices[offset:offset+size]);offset+=size
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            if a!=b:graph[a].add(b);graph[b].add(a)
    return graph


def _projection_mesh_polygons(mesh_name):
    mesh=_mesh_fn(_get_shape_name(mesh_name));sizes,vertices=mesh.getVertices()
    result=[];offset=0
    for size in sizes:
        result.append(list(map(int,vertices[offset:offset+size])));offset+=size
    return result


def _projection_ik_region(mesh_name,influences,indices,points,axis,centre,half_width,mode):
    if mode=='selection':
        return _projection_region_mask(mesh_name,influences,indices,points,mode)
    if mode!='auto':raise RuntimeError('Unknown IK range mode: '+str(mode))
    graph=_projection_mesh_graph(mesh_name)
    band,outer,signed,seed,removed=_projection_connected_band(graph,points,axis,centre,half_width)
    band,outer,gated=_projection_ik_ring_gate(graph,np.asarray(points),np.asarray(axis),np.asarray(centre),half_width,
        _projection_mesh_polygons(mesh_name),band,outer)
    if gated:log.info('[IK Ring V17.5.1] separated branch from projection slab/FWD region')
    log.info('[IK Topology V17.4.3] radius={}, band={}, disconnected_removed={}, fwd_outer={}, seed={}'.format(
        half_width,int(band.sum()),removed,int(outer.sum()),seed))
    return band|outer


def _projection_mesh_boundary_loops(mesh_name):
    mesh=_mesh_fn(_get_shape_name(mesh_name));sizes,vertices=mesh.getVertices()
    polygons=[];offset=0
    for size in sizes:
        polygons.append(list(vertices[offset:offset+size]));offset+=size
    return _projection_boundary_loops(polygons)


def _projection_spine_region(mesh_name,influences,indices,points,chain,half_width,mode,budget=None,return_info=False,reference=None):
    if mode=='selection':
        mask=_projection_region_mask(mesh_name,influences,indices,points,mode)
        return (mask,None) if return_info else mask
    if mode!='auto':raise RuntimeError('Unknown spine range mode: '+str(mode))
    if set(indices)==set(range(len(influences))):
        mask=np.ones(len(points),dtype=bool)
        return (mask,None) if return_info else mask
    frames=[_projection_vp(chain[i],chain[i+1],chain[i+2] if i+2<len(chain) else None,reference)
            for i in range(len(chain)-1)]
    graph=_projection_mesh_graph(mesh_name)
    loops=_projection_mesh_boundary_loops(mesh_name)
    mask,info=_projection_surface_partition(graph,points,frames,half_width,loops,_projection_mesh_polygons(mesh_name))
    if budget is not None:
        mask,tails=_projection_complete_support_tails(graph,mask,budget,info['other_seeds'])
        log.info('[Spine Tails V17.4.6] recovered={}, components={}, skipped={}, vertices={}'.format(
            len(tails['vertices']),tails['components'],tails['skipped_components'],tails['vertices'][:32]))
    log.info('[Spine Surface V17.4.4] anchor_bands={}, torso_seeds={}, other_seeds={}, selected={}'.format(
        info['frames'],len(info['body_seeds']),len(info['other_seeds']),int(mask.sum())))
    log.info('[Spine Cuffs V17.4.6] cuff_parts={}, conservative_parts={}'.format(
        info['cuff_parts'],info['fallback_parts']))
    return (mask,info['body_seeds']) if return_info else mask


def preview_range(mode,mesh_name,target_joint=None,range_mode='auto',r=1.,reference_space='current'):
    """Read-only calculation, returning IDs. Caller may choose to select them."""
    if mode not in ('ik','spine'):raise RuntimeError('范围预览仅用于关节和脊椎模式')
    if not np.isfinite(r) or r<=0:raise RuntimeError('半径倍数必须大于零')
    mesh=_resolve_mesh(mesh_name);skin=_find_skin_cluster(mesh) if mesh else None
    if not skin:raise RuntimeError('当前模型没有 skinCluster')
    fn,path,influences,old=_projection_state(mesh,skin)
    reference=_projection_reference(mesh,skin,reference_space)
    if mode=='ik':
        picked=_projection_influence(target_joint,influences)
        parent=_projection_parent(picked,influences) if picked else None
        if not parent:raise RuntimeError('请先在 UI 拾取有效的子骨骼')
        joints=[picked,parent]
    else:joints=_projection_single_chain(influences)
    indices=[influences.index(j) for j in joints]
    points=_projection_reference_points(_get_shape_name(mesh),reference)
    if mode=='ik':
        axis,centre=_projection_vp(parent,picked,reference=reference)
        half_width=_projection_soft_radius()*r
        mask=_projection_ik_region(mesh,influences,indices,points,axis,centre,half_width,range_mode)
    else:
        half_width=_projection_soft_radius()*r
        mask=_projection_spine_region(mesh,influences,indices,points,joints,half_width,range_mode,
                                      budget=old[:,indices].sum(axis=1),reference=reference)
    mask&=old[:,indices].sum(axis=1)>1e-12
    return np.flatnonzero(mask).tolist()


def preview_assignment_plan(mode,mesh_name,target_joint=None,range_mode='auto',r=1.,reference_space='current'):
    applied=preview_range(mode,mesh_name,target_joint,range_mode,r,reference_space)
    if mode!='ik':return dict(apply_vertices=applied,reset_vertices=[],write_vertices=applied)
    if not applied:return dict(apply_vertices=[],reset_vertices=[],write_vertices=[],cancelled=True)
    mesh=_resolve_mesh(mesh_name);skin=_find_skin_cluster(mesh)
    fn,path,influences,old=_projection_state(mesh,skin)
    picked=_projection_influence(target_joint,influences);parent=_projection_parent(picked,influences)
    indices=[influences.index(picked),influences.index(parent)]
    mask=np.zeros(len(old),dtype=bool);mask[applied]=True
    plan=_projection_ik_assignment_plan(old[:,indices],mask)
    return dict(apply_vertices=applied,reset_vertices=np.flatnonzero(plan['reset_mask']).tolist(),
                write_vertices=np.flatnonzero(plan['write_mask']).tolist())


def _paint_ik(mesh_name,skin_name,xs,ys,r,target_joint=None,range_mode='auto',reference=None):
    fn,path,influences,old=_projection_state(mesh_name,skin_name)
    picked=_projection_influence(target_joint,influences)
    parent=_projection_parent(picked,influences) if picked else None
    if picked is None or parent is None:
        log.warn('关节模式：需要 UI pick 和其上级蒙皮 parent');return False
    v,p=_projection_vp(parent,picked,reference=reference)
    v=(np.asarray(v,dtype=float)/np.linalg.norm(v)).tolist()
    points=_projection_reference_points(_get_shape_name(mesh_name),reference)
    wxs=_ik_xs(v,p,points.tolist())
    radius=_projection_soft_radius()*r*2.
    # The UI supplies inverted ys to Legacy. Restore the rising reference
    # curve, consistently with the existing spine UI semantics.
    fraction=np.asarray(_ik_weights(wxs,xs,[1.-y for y in ys],radius),dtype=float)
    # FWD exterior is a full transfer, independent of custom Bezier endpoints.
    fraction[np.asarray(wxs)>=radius*.5]=1.
    indices=[influences.index(picked),influences.index(parent)]
    mask=_projection_ik_region(mesh_name,influences,indices,points,v,p,radius*.5,range_mode)
    mask&=old[:,indices].sum(axis=1)>1e-12
    if not np.any(mask):
        log.warn('关节模式：范围内没有可分配的父子权重，请预览范围或选择顶点');return False
    plan=_projection_ik_assignment_plan(old[:,indices],mask,fraction)
    ids=np.flatnonzero(plan['write_mask'])
    ok=_projection_write_available(fn,path,old,indices,plan['values'][ids],[picked,parent],ids)
    log.info('[IK Reset V17.4.7] apply={}, reset_outside={}, total_write={}'.format(
        int(plan['apply_mask'].sum()),int(plan['reset_mask'].sum()),len(ids)))
    if ok:_update_paint(skin_name)
    log.info('[Projection V17.4.4 ik] pick={}, source={}, centre={}, v={}, radius={}'.format(picked,parent,p,v,radius))
    return bool(ok)


def _paint_spine(mesh_name,skin_name,xs,ys,r,range_mode='auto',reference=None):
    fn,path,influences,old=_projection_state(mesh_name,skin_name)
    chain=_projection_single_chain(influences)
    points=_projection_reference_points(_get_shape_name(mesh_name),reference)
    vps=[_projection_vp(chain[i],chain[i+1],chain[i+2] if i+2<len(chain) else None,reference)
         for i in range(len(chain)-1)]
    wx=[_ik_xs(v,p,points.tolist()) for v,p in vps]
    indices=[influences.index(j) for j in chain]
    budget=old[:,indices].sum(axis=1)
    radius=_projection_soft_radius()*r*2.
    values=_projection_spine_weights(wx,budget,xs,[1.-y for y in ys],radius)
    mask,anchors=_projection_spine_region(mesh_name,influences,indices,points,chain,radius*.5,range_mode,budget=budget,return_info=True,reference=reference)
    mask&=budget>1e-12
    ids=np.flatnonzero(mask)
    if not len(ids):
        log.warn('脊椎模式：范围内没有链预算，请预览范围或选择顶点');return False
    if anchors is not None:
        values,islands=_projection_spine_connected_influences(
            _projection_mesh_graph(mesh_name),values,mask,anchors)
        log.info('[Spine Islands V17.4.7] rows={}, components={}, skipped_rows={}'.format(
            len(islands['rows']),len(islands['removed_components']),len(islands['skipped_rows'])))
    ok=_projection_write(fn,path,old,indices,values[ids],ids)
    if ok:_update_paint(skin_name)
    log.info('[Projection V17.4.6 spine] unlocked single chain={}'.format(chain))
    return bool(ok)


def _spine_vp(j1, j2):
    p1 = _world_pos(j1)
    p2 = _world_pos(j2)
    v = p2 - p1
    ln = np.linalg.norm(v)
    if ln > 1e-9:
        v = v / ln
    return v.tolist(), p2.tolist()


def _brow_vp(j1, j2):
    p1 = _world_pos(j1)
    p2 = _world_pos(j2)
    p2h = np.array([p2[0], p1[1], p2[2]], dtype=float)
    p = (p1 + p2h) * 0.5
    v = p2h - p1
    ln = np.linalg.norm(v)
    if ln > 1e-9:
        v = v / ln
    return v.tolist(), p.tolist()


def _belt_vp(j1, j2):
    p1 = _world_pos(j1)
    p2 = _world_pos(j2)
    p = (p1 + p2) * 0.5
    v = p2 - p1
    ln = np.linalg.norm(v)
    if ln > 1e-9:
        v = v / ln
    return v.tolist(), p.tolist()


def _build_brow_chain(joints):
    joints = list(joints)
    best_pair = None
    best_d = float('inf')
    for i in range(len(joints)):
        for j in range(i + 1, len(joints)):
            d = float(np.linalg.norm(_world_pos(joints[i]) - _world_pos(joints[j])))
            if d < best_d:
                best_d = d
                best_pair = [joints[i], joints[j]]
    if best_pair is None:
        return joints

    chain = list(best_pair)
    remaining = [j for j in joints if j not in chain]
    while remaining:
        best_next, best_d, best_end = None, float('inf'), 0
        for j in remaining:
            d0 = float(np.linalg.norm(_world_pos(j) - _world_pos(chain[0])))
            d1 = float(np.linalg.norm(_world_pos(j) - _world_pos(chain[-1])))
            if d0 < best_d:
                best_d, best_next, best_end = d0, j, 0
            if d1 < best_d:
                best_d, best_next, best_end = d1, j, 1
        if best_next is None:
            break
        if best_end == 0:
            chain.insert(0, best_next)
        else:
            chain.append(best_next)
        remaining.remove(best_next)
    return chain


def _collect_chain_joints(head_joint, skin_name):
    influences = _get_influences(skin_name)
    if not influences:
        return []

    short_to_orig = {_short_name(j): j for j in influences}

    head_short = _short_name(head_joint)
    if head_short not in short_to_orig:
        log.info(f"[spine] head '{head_joint}' 短名 '{head_short}' "
                 f"不在 influences 里。influences 短名: "
                 f"{sorted(short_to_orig.keys())}")
        return []

    head_orig = short_to_orig[head_short]
    if _is_influence_locked(head_orig):
        log.info(f"[spine] head '{head_orig}' liw 锁定")
        return []

    chain = [head_orig]
    queue = [head_orig]

    while queue:
        cur = queue.pop(0)
        children = cmds.listRelatives(cur, children=True, fullPath=True) or []
        for child in children:
            if cmds.nodeType(child) != 'joint':
                queue.append(child)
                continue

            child_short = _short_name(child)
            if child_short not in short_to_orig:
                queue.append(child)
                continue

            child_orig = short_to_orig[child_short]
            if _is_influence_locked(child_orig):
                continue

            chain.append(child_orig)
            queue.append(child)

    return chain


def _lock_outside_chain(skin_name, chain_set):
    n = 0
    for j in _get_influences(skin_name):
        if j in chain_set:
            continue
        if _is_influence_locked(j):
            continue
        try:
            cmds.setAttr(j + '.liw', 1)
            n += 1
        except Exception:
            pass
    return n


def _paint_split(mesh_name, skin_name, xs, ys, r, vp, head_joint=None):
    if not head_joint:
        log.warn(f"split 模式：未指定链头骨骼（请先在 UI 拾取骨骼）")
        return
    if not cmds.objExists(head_joint):
        log.warn(f"split 模式：'{head_joint}' 不存在")
        return

    influences = _get_influences(skin_name)
    if head_joint not in influences:
        log.warn(f"split 模式：'{head_joint}' 不在 skinCluster influence 里")
        return

    if _is_influence_locked(head_joint):
        log.warn(
            f"split 模式：链头骨骼 '{head_joint}' 的 liw 已锁定。\n"
            f"  请先解锁该骨骼（skinCluster → liw = 0）"
        )
        return False

    # ★ 新增：运行前解锁所有其他骨骼，保证链能完整走通
    #   （避免被其他模式残留的锁定污染）
    n_unlocked = 0
    for j in influences:
        if j == head_joint:
            continue
        if _is_influence_locked(j):
            try:
                cmds.setAttr(j + '.liw', 0)
                n_unlocked += 1
            except Exception:
                pass
    if n_unlocked:
        log.info(
            f"split 模式：运行前解锁 {n_unlocked} 个骨骼，保证链能完整走通")

    joints = _collect_chain_joints(head_joint, skin_name)
    log.info(f"[spine] 从 '{head_joint}' 收集到链: {joints}")
    if len(joints) < 2:
        log.warn(
            f"split 模式：'{head_joint}' 没有 ≥1 个未锁子骨骼，无法形成链。\n"
            f"  请确保：\n"
            f"    1) '{head_joint}' 的 DAG 下有子骨骼\n"
            f"    2) 这些子骨骼是 skinCluster influence\n"
            f"    3) 它们的 liw = 0（未锁）"
        )
        return

    n_locked = _lock_outside_chain(skin_name, set(joints))
    if n_locked:
        log.info(f"split 模式：已自动锁定链外 {n_locked} 个骨骼")

    if vp == 1:
        joints = _build_brow_chain(joints)
        log.info(f"split 模式：brow 重排后链 = {joints}")

    if len(joints) < 2:
        log.warn(f"split 模式：重排后链长 {len(joints)} < 2，终止")
        return

    log.info(f"split 模式：链 = {joints}")

    vp_fn = (_spine_vp, _brow_vp, _belt_vp)[vp]
    vps = [vp_fn(joints[i], joints[i + 1]) for i in range(len(joints) - 1)]

    shape = _get_shape_name(mesh_name)
    pts_arr = _mesh_world_points(shape)
    wx_matrix = [_ik_xs(v, p, pts_arr.tolist()) for v, p in vps]

    fn_skin = _build_fn_skin(skin_name)
    path = _shape_dag(mesh_name)
    index_arr = om.MIntArray([influences.index(j) for j in joints])
    n_vtx = pts_arr.shape[0]
    # ★ 修复：max_weights 用"可用权重空间"（1 - 锁定骨骼权重和），
    # 而不是 joints 当前权重和。首次运行时 joints 权重可能为 0。
    all_idx_for_read = om.MIntArray(list(range(len(influences))))
    all_cur = _read_weights(fn_skin, path, all_idx_for_read, n_vtx)
    locked_indices = [i for i, j in enumerate(influences)
                      if _is_influence_locked(j)]
    if locked_indices:
        locked_sum = all_cur[:, locked_indices].sum(axis=1)
        max_weights = np.clip(1.0 - locked_sum, 0.0, 1.0).tolist()
    else:
        max_weights = [1.0] * n_vtx

    ys_split = [1.0 - y for y in ys]

    r_soft = cmds.softSelect(q=True, softSelectDistance=True) or 1.0
    radius = r_soft * r * 2.0
    flat = _split_weights(wx_matrix, max_weights, xs, ys_split, radius)
    n_layer = len(wx_matrix) + 1
    new_matrix = np.array(flat, dtype=float).reshape(n_vtx, n_layer)
    for jn in joints:
        jpos = _world_pos(jn)
        dist = np.linalg.norm(pts_arr - jpos, axis=1)
        vidx = int(np.argmin(dist))
        row = new_matrix[vidx]
        log.info(
            f"[spine diag] {jn} 位置顶点 {vidx}: "
            f"{[round(float(x), 3) for x in row]}"
            f"  (列顺序 = {joints})"
        )
    _write_weights(fn_skin, path, index_arr, new_matrix)
    _update_paint(skin_name)


# ============================================================================
# 模式：cloth（裙子）
# ============================================================================

# ─── cloth 配置 ─────────────────────────────────────────────
# 主链间混合策略：
#   'soft_direct' → denom = max(象限跨度, soft)，soft 决定影响范围上限
#   'factor'      → denom = 象限跨度 × factor（factor 由 soft 动态计算）
#   'fixed'       → denom = 象限跨度 × CLOTH_EXPAND_FACTOR
CLOTH_MAIN_STRATEGY = 'soft_direct'
CLOTH_EXPAND_FACTOR = 1.5  # 仅 'factor' / 'fixed' 使用

# True = 主链 mix 沿链内 FK 分配到子关节（完整裙骨）
# False = 只给主链根骨骼权重，子关节保持 0
CLOTH_USE_FK = True


def _detect_main_joints(skin_name, exclude=None):
    """从所有未锁 joint 的 DAG 结构识别主关节（可排除 source）。"""
    unlocks = _get_unlocked_influences(skin_name)
    if exclude:
        unlocks = [j for j in unlocks if j != exclude]
    if not unlocks:
        return []
    short_to_orig = {_short_name(j): j for j in unlocks}

    def joint_children(j):
        children = cmds.listRelatives(j, children=True, fullPath=True) or []
        result = []
        for c in children:
            if cmds.nodeType(c) != 'joint':
                continue
            cs = _short_name(c)
            if cs in short_to_orig:
                result.append(short_to_orig[cs])
        return result

    tops = []
    for j in unlocks:
        pars = cmds.listRelatives(j, parent=True, fullPath=True) or []
        has_joint_parent = False
        if pars:
            ps = _short_name(pars[0])
            if ps in short_to_orig:
                has_joint_parent = True
        if not has_joint_parent:
            tops.append(j)

    mains = []
    for top in tops:
        cur = top
        found = False
        for _ in range(64):
            children = joint_children(cur)
            if len(children) == 0:
                mains.append(top)
                found = True
                break
            elif len(children) == 1:
                cur = children[0]
            else:
                mains.extend(children)
                found = True
                break
        if not found:
            mains.append(top)

    seen = set()
    result = []
    for M in mains:
        if M not in seen:
            seen.add(M)
            result.append(M)
    return result


def _paint_cloth(mesh_name, skin_name, xs, ys, r, target_joint=None):
    """裙子：主链间自然衰减 + 可选链内 FK。

    主链识别：DAG 顶层（排除 source）→ 分叉点
    主链混合：
      · soft_direct → denom = max(象限跨度, soft)，soft 决定影响范围上限
      · factor      → denom = 象限跨度 × factor（factor 由 soft 动态计算）
      · fixed       → denom = 象限跨度 × CLOTH_EXPAND_FACTOR
    输出：
      · CLOTH_USE_FK=True  → 主链 mix 沿链内 FK 分配到子关节
      · CLOTH_USE_FK=False → 只给主链根骨骼权重，子关节 = 0
    """
    if not target_joint:
        log.warn("cloth：需要 UI 拾取 source 骨骼（通常在骨骼链根部）")
        return
    if not cmds.objExists(target_joint):
        log.warn(f"cloth：source '{target_joint}' 不存在")
        return

    all_influences = _get_influences(skin_name)
    if target_joint not in all_influences:
        log.warn(f"cloth：source '{target_joint}' 不在 skinCluster influence 里")
        return
    if _is_influence_locked(target_joint):
        log.warn(f"cloth：source '{target_joint}' liw 已锁定")
        return

    source = target_joint

    # ★ 新增：运行前解锁所有其他骨骼，保证主链能完整识别
    #   （避免被其他模式残留的锁定污染）
    n_unlocked = 0
    for j in all_influences:
        if j == source:
            continue
        if _is_influence_locked(j):
            try:
                cmds.setAttr(j + '.liw', 0)
                n_unlocked += 1
            except Exception:
                pass
    if n_unlocked:
        log.info(
            f"cloth 模式：运行前解锁 {n_unlocked} 个骨骼，保证主链能完整识别")

    mains = _detect_main_joints(skin_name, exclude=source)
    if not mains:
        log.warn(
            f"cloth：未识别到主链（source='{source}' 之外的未锁 joint "
            f"没有 DAG 顶层 / 分叉点）")
        return

    log.info(f"[cloth] source='{source}', 识别到 {len(mains)} 个主链: "
             f"{[_short_name(m) for m in mains]}")

    if len(mains) == 1:
        log.info(f"cloth：仅 1 条主链，退化为 spine 模式")
        _paint_split(mesh_name, skin_name, xs, ys, r, vp=0,
                     head_joint=mains[0])
        return

    chains = {}
    for M in mains:
        chain = _collect_chain_joints(M, skin_name)
        if not chain:
            log.warn(f"cloth：主链 '{_short_name(M)}' 为空，跳过")
            continue
        chains[M] = chain

    if len(chains) < 2:
        log.warn("cloth：有效主链 < 2，中止")
        return

    for M, chain in chains.items():
        log.info(f"[cloth]   链 {_short_name(M)}: {len(chain)} 骨 "
                 f"{[_short_name(j) for j in chain]}")

    shape = _get_shape_name(mesh_name)
    pts_arr = _mesh_world_points(shape)
    n_vtx = pts_arr.shape[0]

    all_chain_pos = [
        np.array([_world_pos(j) for j in chain], dtype=np.float64)
        for chain in chains.values()
    ]
    main_roots = list(chains.keys())

    soft_radius = float(
        cmds.softSelect(q=True, softSelectDistance=True) or 1.0)

    sub_dists = []
    for pos in all_chain_pos:
        if len(pos) >= 2:
            lens = np.linalg.norm(pos[1:] - pos[:-1], axis=1)
            sub_dists.extend(lens.tolist())
    avg_sub_dist = float(np.mean(sub_dists)) if sub_dists else 1.0
    fk_radius = max(avg_sub_dist * float(r), 1e-6)

    log.info(f"[cloth] soft={soft_radius:.4f}, strategy={CLOTH_MAIN_STRATEGY}")
    log.info(f"[cloth] fk_radius={fk_radius:.4f} "
             f"(avg_sub={avg_sub_dist:.4f} × r={r})")

    if CLOTH_MAIN_STRATEGY == 'soft_direct':
        mix, _ = _cloth_calc_main_chain_mix(
            pts_arr, all_chain_pos, soft_radius, strategy='soft_direct')
        log.info("[cloth] denom(t) = max(2*R_quadrant(t), soft)")

    elif CLOTH_MAIN_STRATEGY == 'factor':
        _root_pairs = []
        for i in range(len(all_chain_pos)):
            cp = all_chain_pos[i][0]
            prev_cp = all_chain_pos[(i - 1) % len(all_chain_pos)][0]
            next_cp = all_chain_pos[(i + 1) % len(all_chain_pos)][0]
            s = float(np.linalg.norm(cp - prev_cp) +
                      np.linalg.norm(cp - next_cp))
            if s > 1e-6:
                _root_pairs.append(s)
        _median_sum = float(np.median(_root_pairs)) if _root_pairs else 1.0
        _exp = max(1.0, 2.0 * soft_radius / max(_median_sum, 1e-6))
        log.info(f"[cloth] denom(t) = 2*R_quadrant(t) × {_exp:.4f}")
        mix, _ = _cloth_calc_main_chain_mix(
            pts_arr, all_chain_pos, soft_radius,
            strategy='factor', expand_factor=_exp)

    elif CLOTH_MAIN_STRATEGY == 'fixed':
        log.info(f"[cloth] denom(t) = 2*R_quadrant(t) × "
                 f"{CLOTH_EXPAND_FACTOR:.4f}")
        mix, _ = _cloth_calc_main_chain_mix(
            pts_arr, all_chain_pos, soft_radius,
            strategy='fixed', expand_factor=CLOTH_EXPAND_FACTOR)

    else:
        log.warn(f"cloth：未知策略 {CLOTH_MAIN_STRATEGY}")
        return

    all_joints_list = list(all_influences)
    joint_index = {j: i for i, j in enumerate(all_joints_list)}
    computed = np.zeros((n_vtx, len(all_joints_list)), dtype=np.float64)

    if CLOTH_USE_FK:
        ys_fk = [1.0 - y for y in ys]
        for ci, M in enumerate(main_roots):
            chain = chains[M]
            fk = _cloth_fk_chain_weights(
                pts_arr, all_chain_pos[ci], xs, ys_fk, fk_radius)
            scaled = fk * mix[:, ci, None]
            for local_i, jn in enumerate(chain):
                ji = joint_index.get(jn)
                if ji is not None:
                    computed[:, ji] += scaled[:, local_i]
    else:
        for ci, M in enumerate(main_roots):
            chain = chains[M]
            root_jn = chain[0]
            ji = joint_index.get(root_jn)
            if ji is not None:
                computed[:, ji] = mix[:, ci]

    if source in joint_index:
        computed[:, joint_index[source]] = 0.0

    fn_skin = _build_fn_skin(skin_name)
    path = _shape_dag(mesh_name)
    n = int(cmds.polyEvaluate(mesh_name, vertex=True))

    all_idx = om.MIntArray(list(range(len(all_joints_list))))
    cur_w = _read_weights(fn_skin, path, all_idx, n)

    unlocked_idx = [i for i, j in enumerate(all_joints_list)
                    if not _is_influence_locked(j)]
    locked_idx = [i for i, j in enumerate(all_joints_list)
                  if _is_influence_locked(j)]

    if locked_idx:
        locked_sum = cur_w[:, locked_idx].sum(axis=1)
    else:
        locked_sum = np.zeros(n)
    max_w = np.clip(1.0 - locked_sum, 0.0, 1.0)

    row_sum = computed[:, unlocked_idx].sum(axis=1)
    valid = row_sum > 1e-9
    if np.any(valid):
        computed[np.ix_(np.flatnonzero(valid), unlocked_idx)] *= (
            max_w[valid] / row_sum[valid])[:, None]

    if locked_idx:
        computed[:, locked_idx] = cur_w[:, locked_idx]

    for M in main_roots:
        mpos = all_chain_pos[main_roots.index(M)][0]
        d = np.linalg.norm(pts_arr - mpos, axis=1)
        vi = int(np.argmin(d))
        row = computed[vi]
        top3 = np.argsort(-row)[:3]
        info = [(all_joints_list[i], round(float(row[i]), 3))
                for i in top3 if row[i] > 1e-4]
        log.info(f"[cloth diag] {_short_name(M)} 位置 vtx[{vi}] top3: {info}")

    totals = computed.sum(axis=1)
    log.info(f"[cloth diag] 权重总和 min={totals.min():.6f}, "
             f"max={totals.max():.6f}, mean={totals.mean():.6f}")

    cmds.undoInfo(openChunk=True, chunkName="ClothWeight")
    try:
        _write_weights(fn_skin, path, all_idx, computed)
    finally:
        cmds.undoInfo(closeChunk=True)

    verify = _read_weights(fn_skin, path, all_idx, n)
    v_totals = verify.sum(axis=1)
    log.ok(f"[cloth] 写入完成：{n} 顶点，{len(chains)} 主链，"
           f"{sum(len(c) for c in chains.values())} 链内骨骼；"
           f"总和 min={v_totals.min():.6f}, max={v_totals.max():.6f}")

    try:
        _update_paint(skin_name)
    except Exception:
        pass


# ============================================================================
# 模式：eye（眼皮）
# ============================================================================

def _paint_eye(mesh_name, skin_name, xs=None, ys=None, r=None):
    """眼皮模式：BFS 分配顶点到最近的解锁骨骼。

    ★ liw 完全由用户控制，工具不动。
    前置条件：至少 1 个 skin influence 的 liw = 0。
    """
    unlock_joints = _get_unlocked_influences(skin_name)
    if not unlock_joints:
        log.warn(
            "eye 模式需要至少 1 个未锁 joint\n"
            "  请在 Maya 里通过 Paint Skin Weights Tool 手动解锁要参与的眼皮骨骼")
        return

    shape = _get_shape_name(mesh_name)
    pts_arr = _mesh_world_points(shape)
    n_vtx = pts_arr.shape[0]

    fn_skin = _build_fn_skin(skin_name)
    path = _shape_dag(mesh_name)
    all_influences = _get_influences(skin_name)
    index_arr = om.MIntArray([all_influences.index(j) for j in unlock_joints])
    cur_weights = _read_weights(fn_skin, path, index_arr, n_vtx)
    max_weights = cur_weights.sum(axis=1)

    fn_mesh = _mesh_fn(shape)
    joint_vtx = {}
    for ji, jnt in enumerate(unlock_joints):
        jp = om.MPoint(*_world_pos(jnt).tolist())
        try:
            _, face_id = fn_mesh.getClosestPoint(jp, om.MSpace.kWorld)
        except Exception:
            continue
        face_v = fn_mesh.getPolygonVertices(face_id)
        best_v, best_d = None, float('inf')
        for v in face_v:
            vp = fn_mesh.getPoint(v, om.MSpace.kWorld)
            d = (vp - jp).length()
            if d < best_d:
                best_d, best_v = d, v
        if best_v is not None:
            joint_vtx[ji] = {int(best_v)}

    weight_ids = [i for i in range(n_vtx) if max_weights[i] > 1e-4]

    pattern = re.compile(r"\d+")
    ve = {i: set(int(s) for s in pattern.findall(line)[1:])
          for i, line in enumerate(cmds.polyInfo(shape, ve=True) or [])}
    ev = {i: set(int(s) for s in pattern.findall(line)[1:])
          for i, line in enumerate(cmds.polyInfo(shape, ev=True) or [])}
    vv = {v: set(v2 for e in es for v2 in ev.get(e, set())) for v, es in ve.items()}

    remaining = set(weight_ids) - {v for vs in joint_vtx.values() for v in vs}
    while remaining:
        progressed = False
        for ji in list(joint_vtx.keys()):
            vset = joint_vtx[ji]
            new = set()
            for v in vset:
                new.update(vv.get(v, set()) & remaining)
            if new:
                joint_vtx[ji].update(new)
                remaining -= new
                progressed = True
        if not progressed:
            break

    new_matrix = np.zeros((n_vtx, len(unlock_joints)), dtype=float)
    for ji, vset in joint_vtx.items():
        for v in vset:
            new_matrix[v, ji] = max_weights[v]

    _write_weights(fn_skin, path, index_arr, new_matrix)
    _update_paint(skin_name)


# ============================================================================
# 单 influence 写入（ik 使用）
# ============================================================================

def _apply_skin_weights(mesh_name, skin_name, paint_weights):
    paint_jnt = _get_paint_trans(skin_name)
    if not paint_jnt:
        unlocks = _get_unlocked_influences(skin_name)
        if not unlocks:
            log.warn("无法确定写入目标 joint")
            return
        paint_jnt = unlocks[0]

    fn_skin = _build_fn_skin(skin_name)
    path = _shape_dag(mesh_name)
    all_influences = _get_influences(skin_name)
    if paint_jnt not in all_influences:
        log.warn(f"'{paint_jnt}' 不在 skinCluster influence 列表里")
        return
    index = all_influences.index(paint_jnt)
    n_vtx = len(paint_weights)
    matrix = np.asarray(paint_weights, dtype=float).reshape(n_vtx, 1)
    _write_weights(fn_skin, path, om.MIntArray([index]), matrix)
    _update_paint(skin_name)


# ============================================================================
# 主入口
# ============================================================================

MODES = ('soft', 'ik', 'spine', 'brow', 'belt', 'cloth', 'eye')


def paint(mode, mesh_name=None, xs=None, ys=None, r=1.0, target_joint=None, range_mode='auto',reference_space='current'):
    """统一入口。返回 bool：True=已写入，False=跳过或失败。"""
    if mode not in MODES:
        log.warn(f"未知模式: {mode}（可选: {MODES}）")
        return False
    if xs is None:
        xs = (0.0, 0.33, 0.67, 1.0)
    if ys is None:
        ys = (0.0, 0.0, 1.0, 1.0)
    xs = tuple(float(v) for v in xs)
    ys = tuple(float(v) for v in ys)

    if mode in ('soft','ik','spine') and (not np.isfinite(r) or float(r)<=0):
        log.warn('骨骼投影：半径倍数必须大于零');return False

    mesh = _resolve_mesh(mesh_name)
    if not mesh:
        log.warn("未选中 mesh")
        return False
    skin = _find_skin_cluster(mesh)
    if not skin:
        log.warn(f"'{mesh}' 没有 skinCluster")
        return False

    log.info(f"[Legacy] mode={mode}, mesh={mesh}, r={r}, target={target_joint}")
    cmds.undoInfo(openChunk=True)
    try:
        ok = False
        reference=_projection_reference(mesh,skin,reference_space) if mode in ('ik','spine') else None
        if mode == 'soft':
            ok = _paint_soft(mesh, skin, xs, ys, float(r),
                             target_joint=target_joint)
        elif mode == 'ik':
            ok = _paint_ik(mesh, skin, xs, ys, float(r), target_joint=target_joint, range_mode=range_mode,reference=reference)
        elif mode == 'spine':
            ok = _paint_spine(mesh, skin, xs, ys, float(r), range_mode=range_mode,reference=reference)
        elif mode == 'brow':
            ok = _paint_split(mesh, skin, xs, ys, float(r), vp=1,
                              head_joint=target_joint)
        elif mode == 'belt':
            ok = _paint_split(mesh, skin, xs, ys, float(r), vp=2,
                              head_joint=target_joint)
        elif mode == 'cloth':
            ok = _paint_cloth(mesh, skin, xs, ys, float(r),
                              target_joint=target_joint)
        elif mode == 'eye':
            ok = _paint_eye(mesh, skin)

        if ok:
            log.ok(f"[Legacy] {mode} 写入完成")
            return True
        else:
            log.warn(f"[Legacy] {mode} 未写入（内部校验未通过，详见上方日志）")
            return False
    except Exception as e:
        import traceback
        log.error(f"[Legacy] {mode} 失败: {e}\n{traceback.format_exc()}")
        return False
    finally:
        cmds.undoInfo(closeChunk=True)
