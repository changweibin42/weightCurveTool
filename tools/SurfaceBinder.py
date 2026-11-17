# -*- coding: utf-8 -*-
"""SurfaceBinder —— 把 jnt 绑到 mesh 表面 / 从绑定信息重贴回表面

★ P3 双空间语义：
  · wct_* = rest pose 三角面 + 重心（唯一真实绑定信息）
  · loc 的 xform 世界位置 = 当前 pose 显示位置（local_to_display(rest_pos)）
  · rest_surface 由调用方传入；为 None 时退回旧逻辑（当前 pose，向后兼容）
"""
import maya.cmds as cmds
import numpy as np

from ..Logger import log


_POS_ATTRS = ('tx', 'ty', 'tz')

_BIND_ATTRS = (
    ('wct_mesh', 'string'),
    ('wct_id0',  'long'),
    ('wct_id1',  'long'),
    ('wct_id2',  'long'),
    ('wct_u',    'double'),
    ('wct_v',    'double'),
    ('wct_w',    'double'),
)


# ============================================================================
# 内部工具
# ============================================================================

def _ensure_bind_attrs(jnt):
    for name, typ in _BIND_ATTRS:
        if not cmds.attributeQuery(name, node=jnt, exists=True):
            try:
                if typ == 'string':
                    cmds.addAttr(jnt, ln=name, dt='string', k=False)
                else:
                    cmds.addAttr(jnt, ln=name, at=typ, k=False)
            except Exception as e:
                log.warn(f"addAttr 失败 {jnt}.{name} ({typ}): {e}")


def _set_pos_lock(jnt, lock):
    keyable = not lock
    for a in _POS_ATTRS:
        try:
            cmds.setAttr(f"{jnt}.{a}", lock=bool(lock), keyable=keyable, channelBox=keyable)
        except Exception:
            pass


def _get_rest_surface(mesh_name, rest_surface):
    if rest_surface is not None:
        return rest_surface
    from .RestPoseSurface import RestPoseSurface
    return RestPoseSurface(mesh_name)


def _xform_to_display(jnt, rest_pos, rest_surface):
    """把 rest pose 位置映射到当前 pose 显示位置，并设置 jnt。"""
    if rest_surface is None:
        display_pos = rest_pos
    else:
        display_pos = rest_surface.local_to_display(np.asarray([rest_pos], dtype=float))[0]
    _set_pos_lock(jnt, lock=False)
    try:
        cmds.xform(jnt, ws=True, t=(float(display_pos[0]),
                                    float(display_pos[1]),
                                    float(display_pos[2])))
    finally:
        _set_pos_lock(jnt, lock=True)


# ============================================================================
# 对外 API
# ============================================================================

def bind_joint_to_mesh(jnt, mesh_name, pos=None, verbose=False,
                       snap_position=False, rest_surface=None):
    """把 jnt 绑到 mesh 表面。

    pos 是当前 pose 世界坐标（None 时用 jnt 当前位置）。
    wct_* 写的是 rest pose 三角面 + 重心。
    snap_position=True 时把 jnt 精确移到 rest pose 最近点的 display 位置。
    """
    if not cmds.objExists(jnt) or not cmds.objExists(mesh_name):
        return False

    if pos is None:
        pos = cmds.xform(jnt, q=True, ws=True, t=True)

    rs = _get_rest_surface(mesh_name, rest_surface)

    try:
        pos_arr = np.asarray([pos], dtype=float)
        rest_pos = rs.display_to_local(pos_arr)[0]
        result = rs.get_uvw(rest_pos, project_first=True)
        if result is None:
            if verbose:
                log.warn(f"{jnt} 找不到三角面")
            return False
        ids, u, v, w = result
    except Exception as e:
        if verbose:
            log.warn(f"绑定 {jnt} 失败: {e}")
        return False

    if snap_position:
        rest_pos_arr = rs.get_rest_position(ids, (u, v, w))
        try:
            _xform_to_display(jnt, rest_pos_arr, rs)
        except Exception as e:
            if verbose:
                log.warn(f"吸附位置失败 {jnt}: {e}")

    _ensure_bind_attrs(jnt)
    try:
        cmds.setAttr(f"{jnt}.wct_mesh", mesh_name, type='string')
        cmds.setAttr(f"{jnt}.wct_id0", ids[0])
        cmds.setAttr(f"{jnt}.wct_id1", ids[1])
        cmds.setAttr(f"{jnt}.wct_id2", ids[2])
        cmds.setAttr(f"{jnt}.wct_u", u)
        cmds.setAttr(f"{jnt}.wct_v", v)
        cmds.setAttr(f"{jnt}.wct_w", w)
    except Exception as e:
        if verbose:
            log.warn(f"写绑定属性失败: {e}")
        return False
    return True


def bind_from_world_point(jnt, mesh_name, world_point, verbose=False,
                          rest_surface=None):
    """用给定当前 pose 世界点 P 更新 jnt 的 wct_*（不改 loc 位置）。

    返回 (ids, u, v, w) 或 None。
    """
    if not cmds.objExists(jnt) or not cmds.objExists(mesh_name):
        return None

    rs = _get_rest_surface(mesh_name, rest_surface)

    try:
        rest_pos = rs.display_to_local(np.asarray([world_point], dtype=float))[0]
        result = rs.get_uvw(rest_pos, project_first=True)
        if result is None:
            return None
        ids, u, v, w = result
    except Exception as e:
        if verbose:
            log.warn(f"bind_from_world_point 失败 {jnt}: {e}")
        return None

    _ensure_bind_attrs(jnt)
    try:
        cmds.setAttr(f"{jnt}.wct_mesh", mesh_name, type='string')
        cmds.setAttr(f"{jnt}.wct_id0", ids[0])
        cmds.setAttr(f"{jnt}.wct_id1", ids[1])
        cmds.setAttr(f"{jnt}.wct_id2", ids[2])
        cmds.setAttr(f"{jnt}.wct_u", u)
        cmds.setAttr(f"{jnt}.wct_v", v)
        cmds.setAttr(f"{jnt}.wct_w", w)
    except Exception as e:
        if verbose:
            log.warn(f"写绑定属性失败: {e}")
        return None
    return (ids, u, v, w)


def place_from_uvw(jnt, mesh_name, ids, uvw, set_position=True,
                   rest_surface=None):
    """用 rest pose 顶点 + ids + uvw 插值 rest pose 位置。

    set_position=True 时把 jnt 移到该位置的 display 位置。
    返回 rest pose 位置 np.ndarray 或 None。
    """
    if not cmds.objExists(mesh_name):
        return None
    rs = _get_rest_surface(mesh_name, rest_surface)
    try:
        rest_pos = rs.get_rest_position(ids, uvw)
    except Exception:
        return None

    if set_position and cmds.objExists(jnt):
        _xform_to_display(jnt, rest_pos, rs)

    return rest_pos


def read_bind_info(jnt):
    if not cmds.attributeQuery('wct_id0', node=jnt, exists=True):
        return None
    try:
        mesh_name = cmds.getAttr(f"{jnt}.wct_mesh")
        id0 = int(cmds.getAttr(f"{jnt}.wct_id0"))
        id1 = int(cmds.getAttr(f"{jnt}.wct_id1"))
        id2 = int(cmds.getAttr(f"{jnt}.wct_id2"))
        u = float(cmds.getAttr(f"{jnt}.wct_u"))
        v = float(cmds.getAttr(f"{jnt}.wct_v"))
        w = float(cmds.getAttr(f"{jnt}.wct_w"))
    except Exception:
        return None
    return mesh_name, id0, id1, id2, u, v, w


def rebind_joint(jnt, mesh_name, verbose=False, _pts_cache=None,
                 rest_surface=None):
    """用 wct_* 计算 rest pose 位置。返回 (jnt, rest_pos) 或 None。"""
    info = read_bind_info(jnt)
    if info is None:
        if verbose:
            log.warn(f"{jnt} 无绑定信息")
        return None
    _, id0, id1, id2, u, v, w = info

    rs = _get_rest_surface(mesh_name, rest_surface)
    n = rs.core.vtx_count
    if max(id0, id1, id2) >= n:
        if verbose:
            log.warn(f"{jnt} 顶点 id 越界")
        return None

    return (jnt, rs.get_rest_position((id0, id1, id2), (u, v, w)))


def _apply_rebind_results(results, verbose=False, rest_surface=None):
    if not results:
        return 0
    results=[(jnt,pos) for jnt,pos in results if cmds.objExists(jnt)]
    if not results:
        return 0
    mapped=None
    if rest_surface is not None:
        try:
            mapped=np.asarray(rest_surface.local_to_display(
                np.asarray([pos for _,pos in results],dtype=float)),dtype=float)
            if mapped.shape!=(len(results),3):
                mapped=None
        except Exception:
            mapped=None  # Preserve the original per-joint mapping fallback.
    n=0
    for i,(jnt,rest_pos) in enumerate(results):
        try:
            old_display=cmds.xform(jnt,q=True,ws=True,t=True) if verbose else None
            if mapped is None:
                _xform_to_display(jnt,rest_pos,rest_surface)
            else:
                # Mapping already completed; retain the same unlock/write/lock path.
                _xform_to_display(jnt,mapped[i],None)
            n+=1
            if verbose:
                new_display=cmds.xform(jnt,q=True,ws=True,t=True)
                distance=float(np.linalg.norm(np.asarray(new_display)-np.asarray(old_display)))
                log.info(f"{jnt}: display 位移 {distance:.4f}")
        except Exception as exc:
            if verbose:
                log.warn(f"xform 失败 {jnt}: {exc}")
    return n


def infer_mesh_from_joint(jnt, fallback_mesh=None):
    """从 loc 的 wct_mesh attribute 直接取 mesh（P3 更可靠）。"""
    info = read_bind_info(jnt)
    if info is not None:
        mesh_name = info[0]
        if mesh_name and cmds.objExists(mesh_name):
            return mesh_name
    # fallback 到原有逻辑
    pars = cmds.listRelatives(jnt, parent=True, type='transform') or []
    if not pars:
        return fallback_mesh
    jg = pars[0]
    pars2 = cmds.listRelatives(jg, parent=True, type='transform') or []
    if not pars2:
        return fallback_mesh
    set_group = pars2[0]
    children = cmds.listRelatives(set_group, children=True, type='transform') or []
    for c in children:
        if not c.startswith("curves_"):
            continue
        curves_group = f"{set_group}|{c}"
        curves = cmds.listRelatives(curves_group, children=True, type='transform') or []
        for cv in curves:
            if "_ring_" not in cv:
                continue
            short = cv.split('|')[-1]
            base = short.split("_ring_")[0]
            if base.endswith("_fwd") or base.endswith("_bwd"):
                continue
            return base
    return fallback_mesh


def rebind_all_joints(verbose=True, fallback_mesh=None, rest_surface=None):
    """重贴所有 loc 到 rest pose 表面。

    返回重贴数量。
    """
    all_joints = cmds.ls("loc_*", type='joint') or []
    if verbose:
        log.info(f"检查 {len(all_joints)} 个 loc")

    if rest_surface is None:
        # 尝试从第一个有 wct_mesh 的 loc 推断 mesh
        for jnt in all_joints:
            info = read_bind_info(jnt)
            if info and info[0] and cmds.objExists(info[0]):
                rs = _get_rest_surface(info[0], None)
                break
        else:
            if verbose:
                log.warn("无法推断 rest_surface，跳过重贴")
            return 0
    else:
        rs = rest_surface

    no_attr, no_mesh = [], []
    results = []

    for jnt in all_joints:
        try:
            if not cmds.attributeQuery('wct_id0', node=jnt, exists=True):
                no_attr.append(jnt)
                continue
            info = read_bind_info(jnt)
            if info is None:
                no_attr.append(jnt)
                continue
            mesh_name = info[0]
            if not mesh_name or not cmds.objExists(mesh_name):
                mesh_name = infer_mesh_from_joint(jnt, fallback_mesh=fallback_mesh)
                if not mesh_name or not cmds.objExists(mesh_name):
                    no_mesh.append(jnt)
                    continue
            r = rebind_joint(jnt, mesh_name, verbose=verbose, rest_surface=rs)
            if r is not None:
                results.append(r)
        except Exception as e:
            if verbose:
                log.warn(f"处理 {jnt} 时异常: {e}")

    n = _apply_rebind_results(results, verbose=verbose, rest_surface=rs)

    if verbose:
        if no_attr:
            log.warn(f"{len(no_attr)} 个 loc 无 wct_*: {no_attr[:3]}...")
        if no_mesh:
            log.warn(f"{len(no_mesh)} 个 loc 的 mesh 不存在: {no_mesh[:3]}...")
        log.info(f"重贴 {n} 个关节到 rest pose 表面")
    return n

# ============================================================================
# 兼容层：_find_best_triangle（ModifyCurveTool._on_drag 的 fallback 分支用）
# ============================================================================

def _find_best_triangle(fn, face_id, closest_pt, pts_arr):
    """在 face 内遍历三角，返回投影距离最小的 (ids, u, v, w)。

    ★ 兼容旧接口：仅 ModifyCurveTool._on_drag 的 rs is None fallback 分支用。
    ★ 新路径（P3 后）不用此函数，走 RestPoseSurface.get_uvw。
    """
    n_verts = fn.polygonVertexCount(face_id)
    if n_verts < 3:
        return None
    n_tris = n_verts - 2
    c = np.asarray(closest_pt, dtype=float)

    best = None
    best_dist = float('inf')

    for tri_id in range(n_tris):
        try:
            ids = fn.getPolygonTriangleVertices(face_id, tri_id)
        except Exception:
            continue
        if len(ids) != 3:
            continue

        ps = [np.asarray(pts_arr[i], dtype=float) for i in ids]
        v0 = ps[1] - ps[0]
        v1 = ps[2] - ps[0]
        v2 = c - ps[0]

        d00 = float(np.dot(v0, v0))
        d01 = float(np.dot(v0, v1))
        d11 = float(np.dot(v1, v1))
        d20 = float(np.dot(v2, v0))
        d21 = float(np.dot(v2, v1))

        denom = d00 * d11 - d01 * d01
        if abs(denom) < 1e-12:
            continue

        v = (d11 * d20 - d01 * d21) / denom
        w = (d00 * d21 - d01 * d20) / denom
        u = 1.0 - v - w

        proj = u * ps[0] + v * ps[1] + w * ps[2]
        dist = float(np.linalg.norm(proj - c))

        if dist < best_dist:
            best_dist = dist
            best = (list(ids), float(u), float(v), float(w))

    if best is None:
        return None

    ids, u, v, w = best
    u = max(0.0, min(1.0, u))
    v = max(0.0, min(1.0, v))
    w = max(0.0, min(1.0, w))
    s = u + v + w
    if s > 1e-12:
        u, v, w = u / s, v / s, w / s
    return (ids, u, v, w)