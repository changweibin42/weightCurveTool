# -*- coding: utf-8 -*-
"""SkinWeightPainter —— V16.9 全顶点权重总量约束写入。

核心规则：
    * paint_joint 写入目标权重；父骨骼/其他可编辑 influence 分配剩余权重。
    * 锁定 influence 与 paint_joint 后代保持原值。
    * 可编辑 influence 的目标总量 = 1 - 所有固定 influence 的权重和。
    * 因此每个顶点所有 influence 的总权重为 1（固定权重本身超过 1 时无法满足，拒绝写入）。
    * 不再把 edit_joints 原始权重和当作最终 cap，避免原始 total 小于 1 时继续保留缺额。

MFnSkinCluster 的 getWeights / setWeights 使用 physical index，即 influenceObjects() 数组下标。
"""
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.mel as mel
import numpy as np

from ..Logger import log


# ============================================================================
# 内部工具
# ============================================================================

def _get_skin_cluster_name(mesh_name):
    if not mesh_name or not cmds.objExists(mesh_name):
        return None
    shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
    if not shapes:
        return None
    try:
        skin = mel.eval('findRelatedSkinCluster "%s"' % mesh_name)
    except Exception:
        return None
    if not skin or not cmds.objExists(skin):
        return None
    return skin


def _get_shape_dag(mesh_name):
    shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
    if not shapes:
        return None
    try:
        sel = om.MSelectionList()
        sel.add(shapes[0])
        return sel.getDagPath(0)
    except Exception:
        return None


def _build_fn_skin(skin_name):
    try:
        sel = om.MSelectionList()
        sel.add(skin_name)
        skin_obj = sel.getDependNode(0)
        return oma.MFnSkinCluster(skin_obj)
    except Exception:
        return None


def _short_name(name):
    return name.split('|')[-1].split(':')[-1]


def _resolve_full_path(node_name):
    if not node_name:
        return None
    try:
        sel = om.MSelectionList()
        sel.add(node_name)
        return sel.getDagPath(0).fullPathName()
    except Exception:
        return None


def _find_influence_index(fn_skin, jnt):
    """返回 jnt 在 skinCluster 里的 physical index。

    ★ getWeights / setWeights 需要 physical index（= influenceObjects 数组下标）。
    ★ 比对优先用 fullPathName（含命名空间），避免不同命名空间下同名 joint 撞名。
    jnt 不在 influence 内 → 返回 -1。
    """
    if not jnt:
        return -1
    target_path = _resolve_full_path(jnt)
    target_short = _short_name(jnt)
    try:
        infos = fn_skin.influenceObjects()
    except Exception:
        return -1
    for i, dag in enumerate(infos):
        full = dag.fullPathName()
        if target_path is not None:
            if full == target_path:
                return i
        elif _short_name(full) == target_short:
            return i
    return -1


def _get_np_weights(fn_skin, shape_dag, indexes):
    """读一组 influence 的所有顶点权重，返回 (n_verts, n_indexes)。"""
    if not indexes or len(indexes) == 0:
        return None
    comp_fn = om.MFnSingleIndexedComponent()
    component = comp_fn.create(om.MFn.kMeshVertComponent)
    comp_fn.setCompleteData(om.MFnMesh(shape_dag).numVertices)
    weights = fn_skin.getWeights(shape_dag, component, indexes)
    n_verts = len(weights) // len(indexes)
    return np.array(weights, dtype=float).reshape(n_verts, len(indexes))



def is_in_skin(mesh_name, jnt):
    """判断 jnt 是不是 mesh 的 skinCluster influence"""
    skin_name = _get_skin_cluster_name(mesh_name)
    if not skin_name:
        return False
    fn_skin = _build_fn_skin(skin_name)
    if fn_skin is None:
        return False
    return _find_influence_index(fn_skin, jnt) >= 0


def _is_influence_locked(joint_path):
    """读取 influence 的 liw；没有该属性时视为未锁定。"""
    try:
        if not cmds.attributeQuery('liw', node=joint_path, exists=True):
            return False
        return bool(cmds.getAttr(joint_path + '.liw'))
    except Exception:
        # 无法确认锁定状态时采取保守策略，不修改该 influence。
        return True


def paint_edit_joints(mesh_name, paint_joint, w_new, parent_joint=None):
    """写入 paint_joint，并将每个顶点的全 influence 总权重约束为 1。

    固定项：锁定 influence、paint_joint 的 skinCluster 后代。
    可编辑项：未锁定 influence（含 paint_joint）；父骨骼仅在未锁定时参与分配。

    若固定项权重和已经大于 1，则在不修改固定项的前提下无法满足总量约束，
    本函数会拒绝写入，避免静默制造更严重的权重问题。
    """
    skin_name = _get_skin_cluster_name(mesh_name)
    if not skin_name:
        return False, None
    shape_dag = _get_shape_dag(mesh_name)
    if shape_dag is None:
        return False, None
    fn_skin = _build_fn_skin(skin_name)
    if fn_skin is None:
        return False, None

    try:
        influence_dags = fn_skin.influenceObjects()
        influence_paths = [dag.fullPathName() for dag in influence_dags]
    except Exception as exc:
        cmds.warning("读取 skinCluster influence 失败: {}".format(exc))
        return False, None

    influence_count = len(influence_paths)
    if influence_count == 0:
        cmds.warning("skinCluster 没有 influence")
        return False, None

    paint_index = _find_influence_index(fn_skin, paint_joint)
    if paint_index < 0:
        cmds.warning("'{}' 不在 '{}' 的 skinCluster 里".format(paint_joint, mesh_name))
        return False, None
    paint_path = influence_paths[paint_index]

    if _is_influence_locked(paint_path):
        cmds.warning("目标骨骼 '{}' 的 liw 已锁定，取消写入".format(paint_path))
        return False, None

    # 统一使用 fullPathName，避免命名空间或同名 joint 导致错误匹配。
    if parent_joint is None:
        pars = cmds.listRelatives(paint_path, parent=True, type='joint', fullPath=True) or []
        parent_joint = pars[0] if pars else None
    parent_path = _resolve_full_path(parent_joint) if parent_joint else None
    parent_index = influence_paths.index(parent_path) if parent_path in influence_paths else -1

    descendants = cmds.listRelatives(
        paint_path, allDescendents=True, type='joint', fullPath=True
    ) or []
    descendant_paths = set(descendants)
    child_indexes = [
        i for i, path in enumerate(influence_paths)
        if path in descendant_paths and i != paint_index
    ]

    # 锁定项与 paint_joint 后代不写入；父骨骼只有未锁定时才可参与剩余量分配。
    edit_indexes_list = []
    for i, path in enumerate(influence_paths):
        if i == paint_index:
            edit_indexes_list.append(i)
            continue
        if i in child_indexes:
            continue
        if _is_influence_locked(path):
            continue
        edit_indexes_list.append(i)

    if parent_index >= 0 and parent_index not in edit_indexes_list:
        # 父骨骼若被锁定，保持固定，不强行覆盖 liw。
        if not _is_influence_locked(influence_paths[parent_index]):
            edit_indexes_list.append(parent_index)

    # 去重并保持 physical index 顺序。
    edit_indexes_list = sorted(set(edit_indexes_list))
    if paint_index not in edit_indexes_list:
        cmds.warning("目标骨骼不在可编辑 influence 集合中")
        return False, None

    edit_indexes = om.MIntArray(edit_indexes_list)
    paint_index_in_edit = edit_indexes_list.index(paint_index)
    edit_index_set = set(edit_indexes_list)
    fixed_indexes_list = [i for i in range(influence_count) if i not in edit_index_set]

    # 一次读取完整 influence 权重，准确计算固定项总量和原始分布。
    all_indexes = om.MIntArray(list(range(influence_count)))
    try:
        all_weights = _get_np_weights(fn_skin, shape_dag, all_indexes)
        edit_weights = all_weights[:, edit_indexes_list].copy()
    except Exception as exc:
        cmds.warning("读取完整 SkinCluster 权重失败: {}".format(exc))
        return False, None

    if all_weights is None or edit_weights is None:
        cmds.warning("SkinCluster 权重数据为空")
        return False, None

    n_verts = all_weights.shape[0]
    w_arr = np.asarray(w_new, dtype=np.float64).reshape(-1)
    if w_arr.size != n_verts:
        cmds.warning("权重数组长度不匹配: {} vs {}".format(w_arr.size, n_verts))
        return False, None
    w_arr = np.nan_to_num(w_arr, nan=0.0, posinf=1.0, neginf=0.0)

    if fixed_indexes_list:
        fixed_sum = np.sum(all_weights[:, fixed_indexes_list], axis=1)
    else:
        fixed_sum = np.zeros(n_verts, dtype=np.float64)

    # 固定权重超过 1 时，不能在不修改锁定/后代权重的前提下修复。
    impossible = fixed_sum > (1.0 + 1e-5)
    if np.any(impossible):
        bad_count = int(np.count_nonzero(impossible))
        max_fixed = float(np.max(fixed_sum[impossible]))
        cmds.warning(
            "有 {} 个顶点的锁定/固定 influence 权重总和已超过 1（最大 {:.6f}），" \
            "为保护固定权重，本次取消写入。".format(bad_count, max_fixed)
        )
        log.warn("[SkinWeightPainter] 固定权重超限: count={}, max={:.6f}".format(
            bad_count, max_fixed))
        return False, None

    # 可编辑项必须承担剩余总量，而不是沿用原始 edit_weights.sum()。
    target_edit_total = np.maximum(0.0, 1.0 - fixed_sum)

    child_weights = (
        np.sum(all_weights[:, child_indexes], axis=1)
        if child_indexes else np.zeros(n_verts, dtype=np.float64)
    )

    # 将 paint 原权重及其后代权重的比例贡献转移给父骨骼（仅父骨骼可编辑时）。
    if parent_index in edit_indexes_list:
        parent_index_in_edit = edit_indexes_list.index(parent_index)
        edit_weights[:, parent_index_in_edit] += (
            edit_weights[:, paint_index_in_edit] + child_weights
        )

    # paint_joint 本身不参与“剩余权重”的比例分配。
    edit_weights[:, paint_index_in_edit] = 0.0

    # 归一化剩余 influence 的分配比例；零权重顶点优先交给父骨骼，
    # 否则交给第一个非 paint 可编辑 influence。
    row_sums = np.sum(edit_weights, axis=1)
    zero_rows = row_sums < 1e-8
    if np.any(zero_rows):
        fallback_index = None
        if parent_index in edit_indexes_list and parent_index != paint_index:
            fallback_index = edit_indexes_list.index(parent_index)
        else:
            for i in range(len(edit_indexes_list)):
                if i != paint_index_in_edit:
                    fallback_index = i
                    break
        if fallback_index is not None:
            edit_weights[zero_rows, :] = 0.0
            edit_weights[zero_rows, fallback_index] = 1.0
            row_sums[zero_rows] = 1.0

    has_remainder_receiver = np.any(np.arange(len(edit_indexes_list)) != paint_index_in_edit)
    if not has_remainder_receiver:
        # 只有 paint_joint 一个可编辑 influence 时，它必须承担全部剩余量。
        w_paint = target_edit_total.copy()
        new_weights = w_paint[:, None]
    else:
        row_sums = np.sum(edit_weights, axis=1)
        safe_sums = np.where(row_sums > 1e-8, row_sums, 1.0)
        edit_weights /= safe_sums[:, None]

        w_paint = w_arr - child_weights
        w_paint = np.clip(w_paint, 0.0, target_edit_total)
        other = target_edit_total - w_paint

        new_weights = edit_weights * other[:, None]
        new_weights[:, paint_index_in_edit] = w_paint

    # 最终数值保护：可编辑 influence 行和精确等于 target_edit_total。
    # 仅修正浮点残差，不改变各 influence 的相对比例。
    row_total = np.sum(new_weights, axis=1)
    residual = target_edit_total - row_total
    if has_remainder_receiver:
        receiver = (
            edit_indexes_list.index(parent_index)
            if parent_index in edit_indexes_list and parent_index != paint_index
            else next(i for i in range(len(edit_indexes_list)) if i != paint_index_in_edit)
        )
        new_weights[:, receiver] += residual
    else:
        new_weights[:, paint_index_in_edit] += residual

    new_weights = np.maximum(new_weights, 0.0)

    out = om.MDoubleArray(new_weights.reshape(-1).tolist())
    try:
        fn_skin.setWeights(
            shape_dag, om.MObject(), edit_indexes, out, normalize=False
        )
    except Exception as exc:
        cmds.warning("写权重失败: {}".format(exc))
        return False, None

    # 回读完整 influence，验证真正写入后的总和与 paint_joint 权重。
    try:
        verify_weights = _get_np_weights(fn_skin, shape_dag, all_indexes)
        totals = np.sum(verify_weights, axis=1)
        delta = np.abs(totals - 1.0)
        bad = delta > 1e-5
        log.info(
            "[SkinWeightPainter V16.9] Total 验证: min={:.6f}, max={:.6f}, "
            "异常顶点={} / {}".format(
                float(np.min(totals)), float(np.max(totals)),
                int(np.count_nonzero(bad)), n_verts
            )
        )
        actual_paint = verify_weights[:, paint_index].tolist()
        if np.any(bad):
            log.warn(
                "[SkinWeightPainter V16.9] 写入后仍有 Total 偏差，最大误差={:.8f}".format(
                    float(np.max(delta)))
            )
    except Exception as exc:
        log.warn("[SkinWeightPainter V16.9] 写入后回读验证失败: {}".format(exc))
        actual_paint = w_paint.tolist()

    return True, actual_paint


def _pair_targets(original_pair, requested):
    """Only redistribute this pair's original budget; other influences stay fixed."""
    original_pair = np.asarray(original_pair, dtype=float)
    cap = original_pair.sum(axis=1)
    target = np.clip(np.asarray(requested, dtype=float), 0.0, cap)
    return np.column_stack((target, cap - target))


def paint_pair(mesh_name, picked_joint, par_jnt, w_new, valid_mask=None):
    """Boundary mode writes only masked vertices and the explicit parent/child.

    Unmasked calls retain the existing projection-mode writer. Never normalize
    all influences to make a masked pair operation succeed.
    """
    if valid_mask is None:
        return paint_edit_joints(mesh_name, picked_joint, w_new, parent_joint=par_jnt)
    skin = _get_skin_cluster_name(mesh_name)
    dag = _get_shape_dag(mesh_name)
    fn = _build_fn_skin(skin) if skin else None
    if fn is None or dag is None:
        return False, None
    pi = _find_influence_index(fn, picked_joint)
    qi = _find_influence_index(fn, par_jnt)
    if pi < 0 or qi < 0 or pi == qi:
        cmds.warning("父子权重写入目标无效")
        return False, None
    paths = fn.influenceObjects()
    if any(_is_influence_locked(paths[i].fullPathName()) for i in (pi, qi)):
        cmds.warning("父子权重写入目标已锁定")
        return False, None
    indexes = om.MIntArray([pi, qi])
    before = _get_np_weights(fn, dag, indexes)
    requested = np.asarray(w_new, dtype=float).reshape(-1)
    mask = np.asarray(valid_mask, dtype=bool).reshape(-1)
    if before is None or len(mask) != len(before) or len(requested) != len(before):
        cmds.warning("父子权重/顶点范围数组长度不匹配")
        return False, None
    if not np.all(np.isfinite(before)) or not np.all(np.isfinite(requested[mask])):
        cmds.warning("父子权重包含无效数值，取消写入")
        return False, None
    ids = np.flatnonzero(mask)
    if not len(ids):
        return True, before[:, 0].tolist()
    result = _pair_targets(before[ids], requested[ids])
    comp_fn = om.MFnSingleIndexedComponent()
    component = comp_fn.create(om.MFn.kMeshVertComponent)
    comp_fn.addElements(ids.tolist())
    all_indexes = om.MIntArray(list(range(len(paths))))
    saved_rows = np.asarray(fn.getWeights(dag, component, all_indexes), dtype=float).reshape(len(ids), -1)
    try:
        fn.setWeights(dag, component, indexes,
                      om.MDoubleArray(result.reshape(-1).tolist()), normalize=False)
        actual = np.asarray(fn.getWeights(dag, component, indexes), dtype=float).reshape(-1, 2)
        final_rows = np.asarray(fn.getWeights(dag, component, all_indexes), dtype=float).reshape(len(ids), -1)
        others = [i for i in range(len(paths)) if i not in (pi, qi)]
        if (not np.allclose(actual, result, atol=1e-7, rtol=1e-6)
                or not np.allclose(final_rows[:, others], saved_rows[:, others], atol=1e-10, rtol=0.0)):
            # Revert the exact component if skin settings prevented the write.
            fn.setWeights(dag, component, all_indexes,
                          om.MDoubleArray(saved_rows.reshape(-1).tolist()), normalize=False)
            cmds.warning("SkinCluster 未按目标写入父子权重，已恢复本次顶点")
            return False, None
    except Exception:
        # setWeights can fail after modifying data. Restore the selected pair
        # before propagating the error to the caller's undo chunk.
        fn.setWeights(dag, component, all_indexes,
                      om.MDoubleArray(saved_rows.reshape(-1).tolist()), normalize=False)
        raise
    final = before[:, 0].copy()
    final[ids] = actual[:, 0]
    log.info("[父子对 V17.0] 已写入 {}/{} 个顶点，保持每个顶点父子权重总量".format(len(ids), len(mask)))
    return True, final.tolist()
