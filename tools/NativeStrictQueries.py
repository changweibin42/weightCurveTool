# -*- coding: utf-8 -*-
"""Experimental V17.9.0 strict-query backend, gated by Maya parity audit."""
import os
import json
import hashlib
from collections import OrderedDict
import numpy as np
import maya.cmds as cmds

from ..Logger import log, run_native_command

PREFERENCE='wctStrictQueriesAuditV1'
_IDENTITY=None
_PAYLOADS=OrderedDict()
_NOTICE=None


def binary_identity():
    global _IDENTITY
    if not cmds.pluginInfo('wctSurfaceCurve',q=True,loaded=True):raise RuntimeError('Plugin not loaded')
    version=cmds.pluginInfo('wctSurfaceCurve',q=True,version=True)
    if tuple(map(int,version.split('.')[:3]))<(1,8,0):raise RuntimeError('Plugin 1.8.0 or newer required')
    commands=cmds.pluginInfo('wctSurfaceCurve',q=True,command=True) or []
    if 'wctStrictBoundaryQueries' not in commands:raise RuntimeError('Strict-query command unavailable')
    path=cmds.pluginInfo('wctSurfaceCurve',q=True,path=True);stat=os.stat(path)
    stamp=(os.path.abspath(path),stat.st_size,stat.st_mtime_ns,version)
    if _IDENTITY is None or _IDENTITY[0]!=stamp:
        digest=hashlib.sha256()
        with open(path,'rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        _IDENTITY=(stamp,dict(version=version,sha256=digest.hexdigest()))
    return dict(_IDENTITY[1])


def validated_mode():
    if os.environ.get('WCT_NATIVE_STRICT_QUERIES')=='0':return None
    # Only the read-only audit script requests unvalidated experimental modes.
    if os.environ.get('WCT_NATIVE_STRICT_AUDIT')=='1':
        mode=int(os.environ.get('WCT_NATIVE_STRICT_MODE','0'))
        if mode not in (0,1,2,3):raise RuntimeError('Invalid strict audit mode')
        return mode
    if not cmds.optionVar(exists=PREFERENCE):return None
    saved=json.loads(cmds.optionVar(q=PREFERENCE));identity=binary_identity()
    if saved.get('binary')!=identity:return None
    mode=saved.get('mode')
    return mode if type(mode) is int and mode in (0,1,2,3) else None


def remember_audit(mode):
    if type(mode) is not int or mode not in (0,1,2,3):raise ValueError('Invalid audited mode')
    record=dict(binary=binary_identity(),mode=mode)
    cmds.optionVar(sv=(PREFERENCE,json.dumps(record,sort_keys=True)))


def clear_audit():
    if cmds.optionVar(exists=PREFERENCE):cmds.optionVar(remove=PREFERENCE)


def _payload(graph,vertices):
    entry=getattr(graph,'entry',{});key=entry.get('geometry_key')
    edges=_PAYLOADS.get(key) if key is not None else None
    if edges is None:
        pairs=[(a,b) for a,row in enumerate(graph) for b in row if b>a]
        edges=(len(pairs),'\n'.join('{} {}'.format(a,b) for a,b in pairs))
        if key is not None:
            _PAYLOADS[key]=edges;_PAYLOADS.move_to_end(key)
            while len(_PAYLOADS)>4:_PAYLOADS.popitem(last=False)
    return '1 {} {} {}\n{}\n{}'.format(len(graph),edges[0],len(vertices),edges[1],' '.join(map(str,vertices)))


def compute_if_enabled(mesh_name,curve_name,vertices,eps,rings,graph):
    global _NOTICE
    try:
        mode=validated_mode()
        if mode is None:
            if log.details_enabled() and _NOTICE!='python':
                log.info('[WCT Native Strict Backend] Python path; native disabled or this binary has not passed its audit')
                _NOTICE='python'
            return None
        requested=sorted(set(map(int,vertices)))
        if not requested:return {}
        values=run_native_command(cmds.wctStrictBoundaryQueries, mesh_name,curve_name,float(eps),int(rings),mode,_payload(graph,requested))
        values=np.asarray(values,dtype=float)
        if values.size%3 or not np.all(np.isfinite(values)):raise RuntimeError('Invalid native strict result')
        records=values.reshape(-1,3)
        if len(records)!=len(requested)+1 or tuple(records[-1])!=(-1.,1.,float(len(requested))):
            raise RuntimeError('Native strict result protocol mismatch')
        result={};wanted=set(requested)
        for vertex,valid,value in records[:-1]:
            if vertex!=int(vertex) or int(vertex) not in wanted or valid not in (0.,1.) or int(vertex) in result:
                raise RuntimeError('Invalid native strict vertex record')
            result[int(vertex)]=float(value) if valid else None
        if set(result)!=wanted:raise RuntimeError('Incomplete native strict result')
        if log.details_enabled() and _NOTICE!=mode:
            log.info('[WCT Native Strict Backend] enabled mode={}'.format(mode));_NOTICE=mode
        return result
    except Exception as exc:
        notice=str(exc)
        if log.details_enabled() and _NOTICE!=notice:log.info('[WCT Native Strict Backend] Python fallback: '+notice);_NOTICE=notice
        return None
