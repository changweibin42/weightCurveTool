# -*- coding: utf-8 -*-
"""Optional C++ preview. Never connects to authoritative/solver curves.

Meshes are immutable per-drag snapshots in world coordinates. Loc bindings
are DG inputs. No callbacks, timers, or skinCluster connections are created.
"""
import contextlib
import json
import os
import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om
from . import CurveRefine as CR
from .kd_tree_copy import KDTreeCopy

NODE_TYPE = 'wctSurfaceCurve'
_WARNED = False

@contextlib.contextmanager
def _without_undo():
    state = cmds.undoInfo(q=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        yield
    finally:
        cmds.undoInfo(stateWithoutFlush=state)


def _object(node):
    sel = om.MSelectionList()
    sel.add(node)
    return sel.getDependNode(0)


def _mesh_data(vertices, faces):
    faces = np.asarray(faces, dtype=int)
    data = om.MFnMeshData().create()
    om.MFnMesh().create(om.MPointArray(np.asarray(vertices).tolist()),
                       [3] * len(faces), faces.reshape(-1).tolist(), parent=data)
    return data


def _set_data(node, attr, value):
    om.MFnDependencyNode(_object(node)).findPlug(attr, False).setMObject(value)


class FrozenMapping:
    """The existing barycentric+offset mapping, with one display snapshot."""
    def __init__(self, surface, display_points):
        self.core = surface.core
        self.display = KDTreeCopy(np.asarray(display_points), self.core.faces)

    @staticmethod
    def _map(points, source, target):
        pts = np.asarray(points, dtype=float)
        faces, weights = source.get_face_weights(pts)
        triangles = source.faces[faces]
        base = (source.vertices[triangles] * weights[:, :, None]).sum(axis=1)
        result = (target.vertices[triangles] * weights[:, :, None]).sum(axis=1)
        return result + (pts - base)

    def display_to_local(self, points):
        return self._map(points, self.display, self.core)

    def local_to_display(self, points):
        return self._map(points, self.core, self.display)


class NativeCurvePreview:
    def __init__(self, ctx):
        self.ctx = ctx
        self.nodes = []
        self.visibility = None

    @classmethod
    def begin(cls, ctx, joints, kind, mesh_name, display_points):
        global _WARNED
        if os.environ.get('WCT_NATIVE_CURVE_PREVIEW', '1') == '0':
            return None
        if NODE_TYPE not in (cmds.allNodeTypes() or []):
            return None  # Plugin is explicitly loaded by the installation script.
        obj = cls(ctx)
        try:
            with _without_undo():
                obj._build(joints, kind, mesh_name, display_points)
            ctx._wct_curve_drag_active = True
            ctx._wct_curve_weight_pending = False
            return obj
        except Exception as exc:
            obj.close()
            if not _WARNED:
                cmds.warning('[WCT native preview] fallback to release rebuild: {}'.format(exc))
                _WARNED = True
            return None

    def _build(self, joints, kind, mesh_name, display_points):
        rs = self.ctx.rest_surface
        if rs is None or display_points is None:
            raise RuntimeError('Bind/reference surface is required')
        builder = self.ctx.curve_builder
        closed = bool(self.ctx.active_closed)
        if len(joints) < (3 if closed else 2):
            raise RuntimeError('Not enough ordered locs')
        states = [j for j in joints if cmds.attributeQuery(
            'wct_boundaryCorrespondence', node=j, exists=True)]
        if states:
            state = json.loads(cmds.getAttr(states[0] + '.wct_boundaryCorrespondence'))
            uuids = [cmds.ls(j, uuid=True)[0] for j in joints]
            if state.get('version') != 1 or state['uuids'] != uuids or bool(state['closed']) != closed:
                raise RuntimeError('Boundary correspondence changed')
            count = len(state['points'])
            pairs = sorted(zip(state['anchor_indices'], joints))
            ids, joints = zip(*pairs)
        else:
            step = builder.internal_per_segment + 1
            ids = [i * step for i in range(len(joints))]
            count = len(joints) * step if closed else (len(joints)-1)*step+1
        joint_parent = (cmds.listRelatives(joints[0], parent=True, fullPath=True) or [None])[0]
        set_parent = (cmds.listRelatives(joint_parent, parent=True, fullPath=True) or [None])[0] if joint_parent else None
        if set_parent is None:
            raise RuntimeError('Cannot resolve loc set')
        curves = self.ctx.group_mgr.get_curves_from_set(set_parent, kind)
        if not curves:
            raise RuntimeError('Cannot resolve active curve for preview')
        shape = curves[0]
        if cmds.nodeType(shape) != 'nurbsCurve':
            shape = (cmds.listRelatives(shape, s=True, ni=True, type='nurbsCurve', fullPath=True) or [None])[0]
        if shape is None:
            raise RuntimeError('Missing active curve shape')
        # Shapes can share transforms; hide just this shape through a reversible API write.
        visibility = om.MFnDependencyNode(_object(shape)).findPlug('visibility', False)
        if visibility.isLocked or visibility.isConnected:
            raise RuntimeError('Curve shape visibility is locked or connected')
        original_visibility = visibility.asBool()
        node = cmds.createNode(NODE_TYPE, name='wctDragPreviewCompute#', skipSelect=True)
        self.nodes.append(node)
        _set_data(node, 'referenceMesh', _mesh_data(rs.core.vertices, rs.core.faces))
        _set_data(node, 'displayMesh', _mesh_data(display_points, rs.core.faces))
        _set_data(node, 'epIndices', om.MFnIntArrayData().create(list(map(int, ids))))
        params = CR.STRENGTH_PRESETS[builder.strength]
        for name, value in dict(closed=closed, epCount=count,
                                samplesPerSegment=builder.samples_per_segment,
                                iterations=params['lap_iter'], smoothWeight=params['lap_w'],
                                gaussianMode=builder.display_mode == 'gaussian',
                                gaussianSigma=params['gauss_sigma'], gaussianRadius=params['gauss_r'],
                                adhesion=params['adhesion'], surfaceOffset=builder.offset_distance).items():
            cmds.setAttr(node + '.' + name, value)
        for i, joint in enumerate(joints):
            for source, target in [('wct_id0','vertex0'),('wct_id1','vertex1'),
                                   ('wct_id2','vertex2'),('wct_u','weight0'),
                                   ('wct_v','weight1'),('wct_w','weight2')]:
                cmds.connectAttr(joint+'.'+source, '{}.anchors[{}].{}'.format(node,i,target))
        transform = cmds.createNode('transform', name='wctDragPreview#', skipSelect=True)
        self.nodes.append(transform)
        preview_shape = cmds.createNode('nurbsCurve', parent=transform, skipSelect=True)
        cmds.connectAttr(node+'.outputCurve', preview_shape+'.create')
        for attr in ('overrideEnabled','overrideColor','lineWidth','alwaysDrawOnTop'):
            if cmds.attributeQuery(attr,node=shape,exists=True) and cmds.attributeQuery(attr,node=preview_shape,exists=True):
                cmds.setAttr(preview_shape+'.'+attr, cmds.getAttr(shape+'.'+attr))
        # Demand evaluation before hiding the original; invalid output falls back cleanly.
        cmds.getAttr(preview_shape+'.spans')
        self.visibility = (visibility, original_visibility)
        visibility.setBool(False)

    def close(self):
        self.ctx._wct_curve_drag_active = False
        with _without_undo():
            if self.visibility:
                plug, value = self.visibility
                try:
                    plug.setBool(value)
                except RuntimeError:
                    pass  # Source curve may have been deleted by the user.
                self.visibility = None
            for node in reversed(self.nodes):
                if cmds.objExists(node):
                    cmds.delete(node)
            self.nodes = []
