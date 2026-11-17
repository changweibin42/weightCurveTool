# -*- coding: utf-8 -*-
"""CurveLocalPoints —— 曲线的 rest pose EP + offset 后世界位置

曲线两套坐标：
  · wcs_localPoints   —— offset 前的 rest pose EP，权重计算用（P4 读这个）
  · wcs_displayPoints —— offset 后的 rest pose 坐标位置，刷新曲线显示用
                         （mesh 变形后，local_to_display 映射到当前 pose）

refresh_display 流程：
  read wcs_displayPoints → local_to_display(dp) → 设置曲线 EP 位置
  不改 wcs_localPoints / wcs_displayPoints。
"""
import numpy as np
import hashlib
from collections import OrderedDict
import maya.api.OpenMaya as om
import maya.cmds as cmds


LOCAL_ATTR = "wcs_localPoints"
DISPLAY_ATTR = "wcs_displayPoints"


# ============================================================================
# 写
# ============================================================================

def _ensure_attr(node, name):
    if not cmds.attributeQuery(name, node=node, exists=True):
        cmds.addAttr(node, ln=name, dt="doubleArray")


def _write_arr(node, name, arr):
    _ensure_attr(node, name)
    cmds.setAttr(node + "." + name, np.asarray(arr).reshape(-1).tolist(),
                 type="doubleArray")


def write(curve_name, local_points, display_points=None):
    """写两份坐标。

    local_points   —— rest pose EP（无 offset）
    display_points —— offset 后位置（None 时用 local_points）
    """
    if not cmds.objExists(curve_name):
        return
    _write_arr(curve_name, LOCAL_ATTR, local_points)
    if display_points is None:
        display_points = local_points
    _write_arr(curve_name, DISPLAY_ATTR, display_points)


# ============================================================================
# 读
# ============================================================================

def _read_arr(curve_name, name):
    if not cmds.objExists(curve_name):
        return None
    if not cmds.attributeQuery(name, node=curve_name, exists=True):
        return None
    try:
        raw = cmds.getAttr(curve_name + "." + name)
        if not raw:
            return None
        return np.array(raw).reshape(-1, 3)
    except Exception:
        return None


def read_local(curve_name):
    return _read_arr(curve_name, LOCAL_ATTR)


def read_display(curve_name):
    return _read_arr(curve_name, DISPLAY_ATTR)


# 向后兼容：P3.2 用的是 read
def read(curve_name):
    return read_local(curve_name)


def has(curve_name):
    return read_local(curve_name) is not None


# ============================================================================
# 刷新显示
# ============================================================================
   
def _set_curve_eps(curve_name, points):
    """把曲线 EP 位置设为 points。返回 True/False。"""
    eps = cmds.ls(curve_name + ".ep[*]", fl=True)
    if len(eps) == len(points):
        for ep, p in zip(eps, points):
            cmds.xform(ep, ws=True, t=np.asarray(p, dtype=float).tolist())
        return True
    cvs = cmds.ls(curve_name + ".cv[*]", fl=True)
    if len(cvs) == len(points):
        for cv, p in zip(cvs, points):
            cmds.xform(cv, ws=True, t=np.asarray(p, dtype=float).tolist())
        return True
    return False



# Skip only an identical desired display update on the very same unchanged
# shape. Exact hashes: no coordinate quantization or approximate comparisons.
_DISPLAY_CACHE=OrderedDict()

def _display_proof(curve):
    sel=om.MSelectionList();sel.add(curve);dag=sel.getDagPath(0)
    if not dag.hasFn(om.MFn.kNurbsCurve):dag.extendToShape()
    fn=om.MFnNurbsCurve(dag)
    h=hashlib.blake2b(digest_size=32)
    h.update(str((fn.degree,fn.form)).encode('ascii'))
    h.update(np.asarray([(p.x,p.y,p.z,p.w) for p in fn.cvPositions(om.MSpace.kWorld)],dtype=np.float64).tobytes())
    h.update(np.asarray(fn.knots(),dtype=np.float64).tobytes())
    return h.digest()


def _set_display_eps(curve,points):
    desired=np.ascontiguousarray(points,dtype=np.float64)
    target=(desired.shape,hashlib.blake2b(desired.tobytes(),digest_size=32).digest())
    try:
        key=cmds.ls(curve,uuid=True)[0]
        current=_display_proof(curve)
    except Exception:
        return _set_curve_eps(curve,points)
    if _DISPLAY_CACHE.get(key)==(target,current):
        _DISPLAY_CACHE.move_to_end(key)
        return True
    _DISPLAY_CACHE.pop(key,None)
    ok=_set_curve_eps(curve,points)
    if ok:
        try:
            _DISPLAY_CACHE[key]=(target,_display_proof(curve))
            while len(_DISPLAY_CACHE)>32:_DISPLAY_CACHE.popitem(last=False)
        except Exception:
            pass
    return ok


def refresh_display(curve_name, rest_surface):
    """用 wcs_displayPoints + local_to_display 把曲线 EP 刷到当前 pose。

    ★ 直接设 EP（不重建 CV）。周期曲线的 EP 设完后 Maya 自动重算 CV，
      EP 序列 = display_pts 序列，一一对应，无相位漂移。
    """
    if not cmds.objExists(curve_name) or rest_surface is None:
        return False
    dp = read_display(curve_name)
    if dp is None or len(dp) == 0:
        return False
    try:
        display_pts = rest_surface.local_to_display(dp)
    except Exception:
        return False
    return _set_display_eps(curve_name, display_pts)


def refresh_display_all(rest_surface, prefix_filter=None):
    """刷新所有带 wcs_displayPoints 的曲线。

    prefix_filter：只刷新短名以该前缀开头的曲线（支持带 : 命名空间的 mesh_name）。
    返回刷新的曲线数（int）。
    """
    if rest_surface is None:
        return 0
    short_filter = None
    if prefix_filter:
        short_filter = prefix_filter.split('|')[-1].split(':')[-1]
    # Read all saved points first; one mapping call reads/evaluates the mesh
    # once and uses the same pose for every curve in this refresh batch.
    entries = []
    for crv in cmds.ls(type='transform') or []:
        if short_filter:
            short = crv.split('|')[-1].split(':')[-1]
            if not short.startswith(short_filter):
                continue
        if not cmds.attributeQuery(DISPLAY_ATTR, node=crv, exists=True):
            continue
        dp = read_display(crv)
        if dp is not None and len(dp):
            entries.append((crv, dp))
    if not entries:
        return 0
    try:
        mapped = np.asarray(rest_surface.local_to_display(
            np.vstack([dp for _, dp in entries])), dtype=float)
        if mapped.shape != (sum(len(dp) for _, dp in entries), 3):
            raise ValueError('Invalid batched display mapping shape')
    except Exception:
        # Preserve the old per-curve fallback if batching cannot be used.
        n = 0
        for crv, _ in entries:
            try:
                n += bool(refresh_display(crv, rest_surface))
            except Exception:
                pass
        return n
    n = offset = 0
    for crv, dp in entries:
        end = offset + len(dp)
        try:
            n += bool(_set_display_eps(crv, mapped[offset:end]))
        except Exception:
            pass
        offset = end
    return n
