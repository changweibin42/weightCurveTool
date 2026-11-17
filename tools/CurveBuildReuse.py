# -*- coding: utf-8 -*-
"""Reuse unchanged authoritative curve builds, never reuse a region/weight result.

Display CV coordinates are deliberately excluded: refresh_display owns those.
Both saved local/display coordinates AND NURBS topology must still match.
Any incomplete data causes a normal full build, not a guessed cache hit.
"""
import hashlib
import json
import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om
from . import CurveLocalPoints

_SETTINGS = ('strength', 'display_mode', 'samples_per_segment',
             'internal_per_segment', 'curve_line_width', 'lap_color',
             'gauss_color', 'draw_on_top', 'offset_mode', 'offset_distance',
             'min_offset', 'max_offset', 'offset_margin')

def _digest(*values):
    h = hashlib.blake2b(digest_size=20)
    for value in values:
        if isinstance(value, np.ndarray):
            data = np.ascontiguousarray(value)
            h.update(str((data.shape, data.dtype.str)).encode('ascii'))
            h.update(data.tobytes())
        else:
            h.update(json.dumps(value, sort_keys=True, allow_nan=False).encode('utf-8'))
        h.update(b'\0')
    return h.digest()


def _inputs(builder, mesh, joints, closed, prefix):
    rs = builder.rest_surface
    if rs is None:
        return None
    raw = builder._read_joint_rest_positions(joints, mesh)
    if raw is None:
        return None
    uuids = tuple((cmds.ls(j, uuid=True) or [j])[0] for j in joints)
    correspondence = []
    for j in joints:
        attr = 'wct_boundaryCorrespondence'
        correspondence.append(cmds.getAttr(j+'.'+attr) if cmds.attributeQuery(attr,node=j,exists=True) else None)
    key = (uuids, prefix)
    # The project_to_surface implementation reads this evaluated mesh, not
    # merely core.vertices. Include it so edits to the reference mesh invalidate.
    reference_geometry = np.asarray(cmds.xform(
        rs.triangulate_name+'.vtx[*]',q=True,ws=True,t=True),dtype=float)
    # Adaptive/normal display offsets query the evaluated display mesh. A pose
    # change must invalidate reuse even when all reference-space anchors match.
    display_geometry = None
    if builder.offset_mode in ('adaptive', 'normal'):
        display_geometry = np.asarray(cmds.xform(mesh+'.vtx[*]',q=True,ws=True,t=True),dtype=float)
    signature = _digest(reference_geometry, display_geometry, mesh, (cmds.ls(mesh,uuid=True) or [mesh])[0], bool(closed),
                        {name:getattr(builder,name) for name in _SETTINGS},
                        np.asarray(raw,dtype=float), np.asarray(rs.core.vertices,dtype=float),
                        np.asarray(rs.core.faces,dtype=np.int64), correspondence)
    return key, signature


def _proof(curve):
    local = CurveLocalPoints.read_local(curve)
    display = CurveLocalPoints.read_display(curve)
    if local is None or display is None:
        raise ValueError('Missing authoritative curve coordinates')
    sel=om.MSelectionList();sel.add(curve);dag=sel.getDagPath(0)
    if dag.apiType()!=om.MFn.kNurbsCurve:
        dag.extendToShape()
    fn=om.MFnNurbsCurve(dag)
    return _digest(np.asarray(local,dtype=float),np.asarray(display,dtype=float),
                   (fn.degree,fn.form,fn.numCVs),np.asarray(fn.knots(),dtype=float))


def create_if_changed(builder, mesh, joints, closed, prefix):
    """Return (curve, reused). Only successful verified builds are remembered."""
    cache=getattr(builder,'_wct_exact_build_cache',None)
    if cache is None:
        cache=builder._wct_exact_build_cache={}
    try:
        inputs=_inputs(builder,mesh,joints,closed,prefix)
    except Exception:
        inputs=None
    if inputs is not None:
        key, signature=inputs
        previous=cache.get(key)
        if previous is not None and previous[0]==signature:
            try:
                matches=cmds.ls(previous[1],long=True) or []
                if len(matches)==1 and _proof(matches[0])==previous[2]:
                    # Never hide a second display variant that the normal create
                    # would delete. Ambiguity triggers the original full build.
                    other=prefix+'_ring_'+('laplacian' if builder.display_mode=='gaussian' else 'gaussian')
                    if not cmds.objExists(other):
                        builder._current.update(mesh=mesh,joints=list(joints),closed=closed)
                        return matches[0],True
            except Exception:
                pass
        cache.pop(key,None)
    curve=builder.create(mesh,joints,closed=closed,verbose=False,name_prefix=prefix)
    if curve and inputs is not None:
        try:
            if len(cache)>=16:cache.clear()
            cache[inputs[0]]=(inputs[1],cmds.ls(curve,uuid=True)[0],_proof(curve))
        except Exception:
            pass
    return curve,False
