# -*- coding: utf-8 -*-
"""JointState —— joint 状态管理：TRS 通道锁 + liw 影响权重锁

对外动作：

  TRS 锁：
    lock_joint(jnt)             —— 锁定单个 TRS
    unlock_joint(jnt)           —— 解锁单个 TRS
    lock_all_locs()             —— 锁定场景所有 loc_*
    unlock_all_locs()           —— 解锁场景所有 loc_*

  liw 影响权重锁：
    set_liw(jnt, value)                 —— 设置单个 jnt 的 liw
    get_skin_influences(mesh_name=None) —— 取 skinCluster influence 列表
    lock_all_skin_liw(mesh_name=None)   —— 所有蒙皮骨骼 liw = 1
    get_parent_joint(jnt, mesh_name=None)
                                      —— 沿父级链向上找父 joint；指定 mesh 时
                                         跳过不是该 mesh skinCluster influence 的 joint
    apply_liw_on_pick(..., parent_joint=None)
                                      —— 全部蒙皮骨骼 liw=1，指定父 joint liw=0

保留 jnt.radius 可调（拖动拾取依赖它）。
"""
import maya.cmds as cmds
import maya.mel as mel

from ..Logger import log


TRS_ATTRS = ('tx', 'ty', 'tz',
             'rx', 'ry', 'rz',
             'sx', 'sy', 'sz')

# get_parent_joint 的最大向上查找层数（防御死循环）
_PARENT_JOINT_MAX_DEPTH = 64


# ============================================================================
# TRS 锁
# ============================================================================

def lock_joint(jnt):
    """锁定单个 jnt 的 TRS 通道。"""
    return _set_lock(jnt, lock=True)


def unlock_joint(jnt):
    """解锁单个 jnt 的 TRS 通道。"""
    return _set_lock(jnt, lock=False)


def lock_all_locs():
    """锁定场景里所有 loc_* joint。"""
    return _batch(lock=True)


def unlock_all_locs():
    """解锁场景里所有 loc_* joint。"""
    return _batch(lock=False)


def _set_lock(jnt, lock):
    if not jnt or not cmds.objExists(jnt):
        return False
    if cmds.nodeType(jnt) != 'joint':
        return False
    for attr in TRS_ATTRS:
        try:
            cmds.setAttr(
                f"{jnt}.{attr}",
                lock=bool(lock),
                keyable=not lock,
                channelBox=not lock,
            )
        except Exception:
            pass
    return True


def _batch(lock):
    locs = cmds.ls("loc_*", type='joint') or []
    if not locs:
        return 0
    n = 0
    for jnt in locs:
        if _set_lock(jnt, lock):
            n += 1
    tag = "锁定" if lock else "解锁"
    log.info(f"{tag} {n} 个 loc joint")
    return n


# ============================================================================
# liw 影响权重锁
# ============================================================================

def set_liw(jnt, value):
    """设置单个 jnt 的 liw。

    liw = 1  → 锁定该骨骼影响权重（禁止影响）
    liw = 0  → 解锁（允许影响）

    返回 True / False。
    """
    if not jnt or not cmds.objExists(jnt):
        return False
    if cmds.nodeType(jnt) != 'joint':
        return False
    if not cmds.attributeQuery("liw", node=jnt, exists=True):
        return False
    try:
        cmds.setAttr(f"{jnt}.liw", int(value))
        return True
    except Exception:
        return False


def get_skin_influences(mesh_name=None):
    """取 skinCluster 的 influence joint 列表。

    参数：
        mesh_name  None → 遍历场景所有 skinCluster
                   str  → 只取该 mesh 的 skinCluster

    返回：
        去重后的 jnt 名列表（只含 nodeType == 'joint'）。
    """
    skins = []
    if mesh_name:
        if not cmds.objExists(mesh_name):
            return []
        try:
            sk = mel.eval('findRelatedSkinCluster %s' % mesh_name)
            if sk and cmds.objExists(sk):
                skins = [sk]
        except Exception:
            pass
    else:
        skins = cmds.ls(type='skinCluster') or []

    out = []
    seen = set()
    for sk in skins:
        if not sk or not cmds.objExists(sk):
            continue
        try:
            inf = cmds.skinCluster(sk, q=True, influence=True) or []
        except Exception:
            continue
        for jnt in inf:
            if not jnt or jnt in seen:
                continue
            if cmds.objExists(jnt) and cmds.nodeType(jnt) == 'joint':
                seen.add(jnt)
                out.append(jnt)
    return out


def _long_name(node):
    """尽量将 DAG 节点解析为完整路径，避免同名节点误匹配。"""
    if not node or not cmds.objExists(node):
        return None
    result = cmds.ls(node, long=True) or []
    return result[0] if result else node


def get_parent_joint(jnt, mesh_name=None):
    """沿 transform 层级向上查找父级 joint。

    mesh_name=None：保持旧行为，返回遇到的第一个父级 joint。

    指定 mesh_name：只返回该 mesh 对应 skinCluster 中的 influence joint。
    中途遇到的 locator joint、辅助 joint 或其他非 influence joint 会被跳过，
    继续沿 DAG 父级链向上查找。

    到达场景根仍未找到符合条件的 joint → 返回 None。
    """
    if not jnt or not cmds.objExists(jnt):
        return None

    influence_paths = None
    if mesh_name is not None:
        influences = get_skin_influences(mesh_name)
        influence_paths = set()
        for influence in influences:
            full = _long_name(influence)
            if full:
                influence_paths.add(full)

        if not influence_paths:
            return None

    cur = jnt
    for _ in range(_PARENT_JOINT_MAX_DEPTH):
        pars = cmds.listRelatives(
            cur, parent=True, fullPath=True) or []
        if not pars:
            return None

        par = pars[0]
        if not cmds.objExists(par):
            return None

        if cmds.nodeType(par) == 'joint':
            if influence_paths is None:
                return par

            par_full = _long_name(par)
            if par_full in influence_paths:
                return par

        # 不论父节点是不是 joint，都继续沿 DAG 层级向上走。
        cur = par

    cmds.warning(
        f"get_parent_joint: 向上查找超过 {_PARENT_JOINT_MAX_DEPTH} 层仍未"
        f"找到符合条件的父 joint（起点 {jnt}）。可能存在异常深的层级或循环。")
    return None


def apply_liw_on_pick(picked_joint, mesh_name=None, verbose=True,
                      parent_joint=None):
    """解锁 picked_joint 和 parent_joint，其他骨骼不动。

    行为：
        · picked_joint（要写入权重的子骨骼）liw = 0
        · parent_joint（接收剩余权重的父骨骼）liw = 0
        · 其他 influence 保持原 liw 状态（不主动锁定，不污染用户手配）

    调用场景：
        · 曲线边界 soft/ik 模式切换 / 重绘前
        · 保证 paint_edit_joints 能正常写入

    参数：
        picked_joint  UI 输入/拾取的子骨骼
        mesh_name     指定时只处理该 mesh 的 skinCluster
        verbose       是否输出日志
        parent_joint  可选，调用方已筛选并验证的父级蒙皮骨骼。
                      未传入时，按 mesh_name 重新向上查找。

    返回：
        实际解锁的父 joint 名（或 None）。
    """
    if parent_joint is None:
        par_jnt = get_parent_joint(picked_joint, mesh_name=mesh_name)
    else:
        par_jnt = parent_joint

    # 防止外部传入 locator 或非当前 mesh influence。
    if par_jnt:
        if not cmds.objExists(par_jnt) or cmds.nodeType(par_jnt) != 'joint':
            par_jnt = None
        elif mesh_name and _long_name(par_jnt) not in {
                _long_name(x) for x in get_skin_influences(mesh_name)}:
            par_jnt = None

    influences = get_skin_influences(mesh_name)

    n_set = 0
    n_skip = 0
    picked_long = _long_name(picked_joint)
    par_long = _long_name(par_jnt) if par_jnt else None
    targets = {picked_long}
    if par_long and par_long != picked_long:
        targets.add(par_long)

    n_set = 0
    n_skip = 0
    for jnt in influences:
        if not cmds.attributeQuery("liw", node=jnt, exists=True):
            n_skip += 1
            continue
        jnt_long = _long_name(jnt)
        if jnt_long not in targets:
            continue
        try:
            cmds.setAttr(f"{jnt}.liw", 0)
            n_set += 1
        except Exception:
            pass

    if verbose:
        unlocked_names = [picked_joint]
        if par_jnt and par_jnt != picked_joint:
            unlocked_names.append(par_jnt)
        log.info(
            f"liw: 解锁 {n_set} 个骨骼: {', '.join(unlocked_names)}，"
            f"其他骨骼保持原状态")
        if n_skip:
            log.info(f"（跳过 {n_skip} 个无 liw 属性的 joint）")

    return par_jnt