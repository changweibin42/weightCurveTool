# -*- coding: utf-8 -*-
"""V17.9.1 audited batch replacement for the original rest-mesh point projection."""
import os
import json
import numpy as np
import maya.cmds as cmds

from ..Logger import log, run_native_command
from . import NativeStrictQueries as StrictDriver

PREFERENCE='wctProjectionAuditV1'
_NOTICE=None
_BATCHES=0
_FALLBACKS=0


def binary_identity():
    identity=StrictDriver.binary_identity()
    if tuple(map(int,identity['version'].split('.')[:3]))<(1,8,1):
        raise RuntimeError('Projection command requires plugin 1.8.1 or newer')
    if 'wctProjectPoints' not in (cmds.pluginInfo('wctSurfaceCurve',q=True,command=True) or []):
        raise RuntimeError('wctProjectPoints is unavailable')
    return identity


def validated_mode():
    if os.environ.get('WCT_NATIVE_PROJECT')=='0':return None
    if os.environ.get('WCT_NATIVE_PROJECT_AUDIT')=='1':
        mode=int(os.environ.get('WCT_NATIVE_PROJECT_MODE','0'))
        if mode not in (0,2):raise RuntimeError('Invalid projection audit mode')
        return mode
    if not cmds.optionVar(exists=PREFERENCE):return None
    record=json.loads(cmds.optionVar(q=PREFERENCE))
    if record.get('binary')!=binary_identity():return None
    mode=record.get('mode')
    return mode if type(mode) is int and mode in (0,2) else None


def remember_audit(mode):
    if type(mode) is not int or mode not in (0,2):raise ValueError('Invalid projection mode')
    cmds.optionVar(sv=(PREFERENCE,json.dumps(dict(binary=binary_identity(),mode=mode),sort_keys=True)))


def clear_audit():
    if cmds.optionVar(exists=PREFERENCE):cmds.optionVar(remove=PREFERENCE)


def compute_if_enabled(mesh_name,points):
    global _NOTICE,_BATCHES,_FALLBACKS
    arr=np.asarray(points,dtype=float)
    if arr.ndim!=2 or arr.shape[1]!=3 or len(arr)==0:return None
    # Small EP/control batches stay on the original path; native setup costs
    # more than a few point queries. The audit exercises all captured sizes.
    if len(arr)<32 and os.environ.get('WCT_NATIVE_PROJECT_AUDIT')!='1':return None
    try:
        mode=validated_mode()
        if mode is None:
            if log.details_enabled() and _NOTICE!='python':
                log.info('[WCT Native Projection] Python path; no enabled audit for this binary');_NOTICE='python'
            return None
        if not np.all(np.isfinite(arr)):raise RuntimeError('Non-finite projection input')
        payload='1 {}\n{}'.format(len(arr),'\n'.join('{:.17g} {:.17g} {:.17g}'.format(*row) for row in arr))
        result=np.asarray(run_native_command(cmds.wctProjectPoints, mesh_name,mode,payload),dtype=float)
        if result.size%4 or not np.all(np.isfinite(result)):raise RuntimeError('Invalid projection result')
        rows=result.reshape(-1,4)
        if len(rows)!=len(arr)+1 or tuple(rows[-1])!=(-1.,1.,float(len(arr)),-2.):
            raise RuntimeError('Projection result protocol mismatch')
        faces=rows[:-1,3]
        if np.any(faces<0) or np.any(faces!=np.floor(faces)):raise RuntimeError('Invalid projection face')
        if log.details_enabled() and _NOTICE!=mode:log.info('[WCT Native Projection] enabled mode={}'.format(mode));_NOTICE=mode
        _BATCHES+=1
        return rows[:-1,:3].copy()
    except Exception as exc:
        _FALLBACKS+=1
        notice=str(exc)
        if log.details_enabled() and _NOTICE!=notice:log.info('[WCT Native Projection] Python fallback: '+notice);_NOTICE=notice
        return None
