# -*- coding: utf-8 -*-
"""V17.9.2: enable per-command query reuse only after a binary-bound audit."""
import json
import os
import maya.cmds as cmds
from .NativeStrictQueries import binary_identity

PREFERENCE = 'wct_native_query_reuse_audit_v1'
MODE_KEYS = ('WCT_NATIVE_ACCEL_POINT', 'WCT_NATIVE_ACCEL_NORMAL',
             'WCT_NATIVE_BVH_POINT', 'WCT_NATIVE_FACE_QUERY')


def _profile():
    return [os.environ.get(key, '0') for key in MODE_KEYS]


def _supported(identity):
    try:
        return tuple(int(v) for v in identity['version'].split('.')[:3]) >= (1, 8, 2)
    except (KeyError, TypeError, ValueError):
        return False


def mode_if_enabled():
    if os.environ.get('WCT_NATIVE_QUERY_REUSE') == '0':
        return 0
    try:
        identity = binary_identity()
        if not _supported(identity):
            return 0
        # Temporary explicit access for the read-only comparison script only.
        if os.environ.get('WCT_NATIVE_QUERY_REUSE_AUDIT') == '1':
            return 1 if os.environ.get('WCT_NATIVE_QUERY_REUSE') == '1' else 0
        if not cmds.optionVar(exists=PREFERENCE):
            return 0
        record = json.loads(cmds.optionVar(q=PREFERENCE))
        if (record.get('schema') == 1 and record.get('binary') == identity
                and record.get('profile') == _profile()):
            return 1 if record.get('mode') == 1 else 0
    except Exception:
        pass
    return 0


def remember_audit():
    identity = binary_identity()
    if not _supported(identity):
        raise RuntimeError('Query reuse requires wctSurfaceCurve 1.8.2 or newer')
    record = dict(schema=1, binary=identity, profile=_profile(), mode=1)
    cmds.optionVar(sv=(PREFERENCE, json.dumps(record, sort_keys=True)))


def clear_audit():
    if cmds.optionVar(exists=PREFERENCE):
        cmds.optionVar(remove=PREFERENCE)
