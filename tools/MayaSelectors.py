# -*- coding: utf-8 -*-
"""Maya 选择集 / 场景查询工具"""
import maya.cmds as cmds
import re


def get_selected_mesh_transform():
    """返回当前选择里的 mesh transform（支持选中 shape 的情况）"""
    sel = cmds.ls(selection=True)
    if not sel:
        return None
    for node in sel:
        node_type = cmds.nodeType(node)
        if node_type == 'transform':
            shapes = cmds.listRelatives(node, shapes=True, type='mesh')
            if shapes:
                return node
        else:
            pars = cmds.listRelatives(node, parent=True)
            if pars:
                par = pars[0]
                shapes = cmds.listRelatives(par, shapes=True, type='mesh')
                if shapes:
                    return par
    return None


def get_selected_joint():
    """返回当前选择里的第一个 joint"""
    sel = cmds.ls(selection=True, type='joint')
    return sel[0] if sel else None


# ============================================================================
# 场景查询
# ============================================================================

def sanitize_name(name):
    """Replace characters that cannot appear in a generated loc prefix."""
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)


def curve_prefix(mesh_name, kind):
    """Use the mesh name directly for the main curve."""
    if kind == 'main':
        return mesh_name
    return f"{mesh_name}_{kind}"


def _loc_sort_key(loc):
    """从 loc 名中提取末尾序号（用于稳定排序）。

    ★ 支持三种后缀：
        loc_<mesh>_<joint>_<setnum>_001         → 1
        loc_<mesh>_<joint>_<setnum>_001_fwd     → 1
        loc_<mesh>_<joint>_<setnum>_001_bwd     → 1

    ★ 用于取代"隐式依赖 Maya listRelatives 返回顺序"的做法——
      listRelatives 不保证顺序，一旦乱序 Catmull-Rom 插值会打结。
    """
    base = loc.split('|')[-1]
    if base.endswith('_fwd'):
        base = base[:-4]
    elif base.endswith('_bwd'):
        base = base[:-4]
    m = re.search(r'_(\d+)$', base)
    return int(m.group(1)) if m else 0


def find_main_locs(mesh_name):
    """从场景里筛选出指定 mesh 的主 loc（排除 _fwd / _bwd 后缀）。

    ★ loc 名里的 mesh 前缀经过了 sanitize_name（见 CreateCurveFlow），
      所以这里也必须用 sanitize_name 拼前缀，
      否则 mesh 名含 : / | / 空格等字符时前缀对不上。

    参数：
        mesh_name  网格名（可含命名空间 / 路径）
    返回：
        按末尾序号排序后的 loc 名列表
    """
    mesh_clean = sanitize_name(mesh_name)
    prefix = f"loc_{mesh_clean}_"
    all_joints = cmds.ls(f"{prefix}*", type='joint') or []
    locs = [loc for loc in all_joints
            if not (loc.endswith("_fwd") or loc.endswith("_bwd"))]
    locs.sort(key=_loc_sort_key)
    return locs