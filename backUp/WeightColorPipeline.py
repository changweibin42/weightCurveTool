# -*- coding: utf-8 -*-
"""WeightColorPipeline V17.3 —— 权重计算 + Total=1 写入诊断 + 顶点颜色可视化"""
import traceback
import inspect
import hashlib

import numpy as np
import maya.cmds as cmds
import maya.api.OpenMaya as om

from ..Logger import log
from .BoundaryWriteHistory import prepare_write, committed_state
from .SkinWeightPainter import (
    is_in_skin, paint_pair,
    _get_skin_cluster_name, _get_shape_dag, _build_fn_skin,
    _find_influence_index, _get_np_weights,
)


def _undo_silently():
    try:
        cmds.undo()
    except Exception:
        pass


def _log_skin_total_stats(mesh_name, label):
    """只读统计 SkinCluster 每个顶点所有 influence 的总权重。"""
    try:
        skin = _get_skin_cluster_name(mesh_name)
        if not skin:
            log.warn("[Total诊断] {}: 未找到 skinCluster".format(label))
            return
        fn_skin = _build_fn_skin(skin)
        shape_dag = _get_shape_dag(mesh_name)
        if fn_skin is None or shape_dag is None:
            log.warn("[Total诊断] {}: 无法创建 SkinCluster API".format(label))
            return
        count = len(fn_skin.influenceObjects())
        if count <= 0:
            return
        indexes = om.MIntArray(list(range(count)))
        matrix = _get_np_weights(fn_skin, shape_dag, indexes)
        if matrix is None or matrix.size == 0:
            return
        totals = np.sum(matrix, axis=1)
        bad_low = int(np.count_nonzero(totals < 0.99999))
        bad_high = int(np.count_nonzero(totals > 1.00001))
        log.info(
            "[Total诊断] {}: min={:.6f}, max={:.6f}, mean={:.6f}, "
            "低于1={}，高于1={}，顶点数={}".format(
                label, float(np.min(totals)), float(np.max(totals)),
                float(np.mean(totals)), bad_low, bad_high, len(totals)
            )
        )
    except Exception:
        log.warn("[Total诊断] {} 统计失败:\n{}".format(label, traceback.format_exc()))


def _read_original_weights(mesh_name, jnt):
    """读 jnt 在 mesh 上当前的权重（用于 in_band 外保留原值）。"""
    skin = _get_skin_cluster_name(mesh_name)
    if not skin:
        return None
    fn_skin = _build_fn_skin(skin)
    shape_dag = _get_shape_dag(mesh_name)
    if fn_skin is None or shape_dag is None:
        return None
    idx = _find_influence_index(fn_skin, jnt)
    if idx < 0:
        return None
    arr = om.MIntArray([idx])
    w_mat = _get_np_weights(fn_skin, shape_dag, arr)
    if w_mat is None:
        return None
    return w_mat[:, 0]

def _ensure_write_targets_unlocked(picked, par):
    """解锁 picked 和 par 的 liw，保证 paint_edit_joints 能写入。

    只在写入前调用（init_weights_for_picked / update_combined_vertex_colors）。
    其他骨骼完全不动（尊重用户手动配置）。

    返回实际解锁的数量。
    """
    targets = []
    if picked and picked not in targets:
        targets.append(picked)
    if par and par != picked and par not in targets:
        targets.append(par)

    n = 0
    for jnt in targets:
        if not jnt or not cmds.objExists(jnt):
            continue
        if not cmds.attributeQuery('liw', node=jnt, exists=True):
            continue
        try:
            if cmds.getAttr(jnt + '.liw'):
                cmds.setAttr(jnt + '.liw', 0)
                n += 1
        except Exception:
            pass
    if n:
        log.info(f"写入前 liw 检查：解锁 {n} 个骨骼 {targets}")
    return n

def init_weights_for_picked(ctx, mesh_name):
    """把已拾取骨骼权重清零（picked=0，par 接管全部 cap），顶点颜色随之全黑。"""
    picked = ctx.reference_joint
    par = ctx.parent_joint

    # ★ 新增：写入前解锁 picked 和 par，保证 paint_edit_joints 能写入
    _ensure_write_targets_unlocked(picked, par)
    if not picked or not par:
        log.warn("未拾取骨骼，跳过权重初始化")
        return False

    if not cmds.objExists(mesh_name):
        log.warn("模型 '{}' 不存在".format(mesh_name))
        return False

    if not is_in_skin(mesh_name, picked):
        log.warn("拾取骨骼 '{}' 不在 '{}' 蒙皮内".format(picked, mesh_name))
        return False
    if not is_in_skin(mesh_name, par):
        log.warn("父关节 '{}' 不在 '{}' 蒙皮内".format(par, mesh_name))
        return False

    try:
        n = int(cmds.polyEvaluate(mesh_name, vertex=True))
    except Exception:
        log.warn("无法获取 '{}' 顶点数".format(mesh_name))
        return False
    if n <= 0:
        return False

    w_zero = [0.0] * n

    cmds.undoInfo(openChunk=True)
    try:
        ok, w_real = paint_pair(mesh_name, picked, par, w_zero)
    except Exception as e:
        cmds.undoInfo(closeChunk=True)
        _undo_silently()
        log.warn("权重初始化异常: {}".format(e))
        return False
    cmds.undoInfo(closeChunk=True)

    if not ok:
        log.warn("权重初始化失败")
        return False

    if hasattr(ctx, '_boundary_write_history'):
        ctx._boundary_write_history.clear()
    ctx.color_display.apply(mesh_name, w_real)
    log.ok("权重初始化完成（picked 清零，顶点色全黑）")
    return True


def _active_boundary_curve_names(ctx):
    """Resolve curves in the active set, avoiding same-name curves in old sets."""
    names = []
    for kind in ('main', 'fwd', 'bwd'):
        groups = ctx.group_mgr.active_groups.get(kind)
        if not groups:
            return None
        transforms = cmds.listRelatives(groups[1], children=True, fullPath=True,
                                        type='transform') or []
        curves = [node for node in transforms if cmds.listRelatives(
            node, shapes=True, noIntermediate=True, type='nurbsCurve')]
        preferred = [node for node in curves if node.endswith('_ring_' + ctx.weight_calc.curve_suffix)]
        if len(preferred) == 1:
            names.append(preferred[0])
        elif len(curves) == 1:
            names.append(curves[0])
        else:
            raise RuntimeError('当前组 {} 曲线缺失或不唯一'.format(kind))
    return tuple(names)


def _log_pipeline_diagnostics(ctx, mesh_name, picked, par_jnt):
    if not getattr(ctx, 'verbose_diagnostics', False):
        return
    try:
        log.info("========== WeightColorPipeline 诊断开始 ==========")
        log.info("[诊断] Pipeline 文件: {}".format(inspect.getfile(update_combined_vertex_colors)))
        log.info("[诊断] Mesh: {} | exists={}".format(mesh_name, cmds.objExists(mesh_name)))
        log.info("[诊断] ctx.reference_joint / 子骨骼: {}".format(picked))
        log.info("[诊断] ctx.parent_joint / 父骨骼: {}".format(par_jnt))

        for _label, _node in (("子骨骼", picked), ("父骨骼", par_jnt)):
            if _node and cmds.objExists(_node):
                try:
                    _types = cmds.nodeType(_node)
                except Exception:
                    _types = "<读取失败>"
                try:
                    _paths = cmds.ls(_node, long=True) or []
                except Exception:
                    _paths = []
                try:
                    _parents = cmds.listRelatives(_node, parent=True, fullPath=True) or []
                except Exception:
                    _parents = []
                log.info("[诊断] {}: type={}, long={}, dagParent={}".format(
                    _label, _types, _paths, _parents))
            else:
                log.warn("[诊断] {}节点不存在或为空: {}".format(_label, _node))

        _skin_name = _get_skin_cluster_name(mesh_name)
        log.info("[诊断] SkinCluster: {}".format(_skin_name))
        if _skin_name:
            try:
                _skin_infs = cmds.skinCluster(_skin_name, query=True, influence=True) or []
            except Exception as _e:
                _skin_infs = []
                log.warn("[诊断] cmds.skinCluster 查询 influence 异常: {}".format(_e))
            log.info("[诊断] influence 数量: {}".format(len(_skin_infs)))
            log.info("[诊断] influence 列表: {}".format(_skin_infs))

            _fn = _build_fn_skin(_skin_name)
            if _fn:
                for _label, _node in (("子骨骼", picked), ("父骨骼", par_jnt)):
                    try:
                        _idx = _find_influence_index(_fn, _node) if _node else -1
                    except Exception as _e:
                        _idx = -999
                        log.warn("[诊断] {} influence index 查询异常: {}".format(_label, _e))
                    log.info("[诊断] {} influence physicalIndex: {}".format(_label, _idx))
            else:
                log.warn("[诊断] 无法创建 MFnSkinCluster")
        else:
            log.warn("[诊断] 未找到当前 Mesh 的 skinCluster")
        log.info("========== WeightColorPipeline 诊断结束 ==========")
    except Exception:
        log.warn("[诊断] 诊断日志自身发生异常:\n{}".format(traceback.format_exc()))


def update_combined_vertex_colors(ctx, mesh_name, xs=None, ys=None,
                                  closed=None, joints=None):
    # Keep the latest UI parameters, but defer writing until authoritative curves
    # have been rebuilt on release. Preview geometry never enters the solver.
    if getattr(ctx, '_wct_curve_drag_active', False):
        if xs is not None:
            ctx.last_xs = tuple(xs)
        if ys is not None:
            ctx.last_ys = tuple(ys)
        if closed is not None:
            ctx.active_closed = bool(closed)
        ctx._wct_curve_weight_pending = True
        return None

    # ★ 新增：写入前解锁 picked 和 par，保证 paint_edit_joints 能写入
    _ensure_write_targets_unlocked(ctx.reference_joint, ctx.parent_joint)

    if xs is not None:
        ctx.last_xs = tuple(xs)
    if ys is not None:
        ctx.last_ys = tuple(ys)
    if closed is not None:
        ctx.active_closed = bool(closed)

    if joints is None:
        joints = ctx.group_mgr.get_joints_from_active('main')
    if not joints:
        cmds.warning("无 joints")
        return False

    picked = ctx.reference_joint
    par_jnt = ctx.parent_joint

    _log_pipeline_diagnostics(ctx, mesh_name, picked, par_jnt)

    if not picked:
        cmds.warning("尚未拾取骨骼，无法写入权重")
        return False
    if not cmds.objExists(picked):
        cmds.warning("拾取骨骼 '{}' 不存在".format(picked))
        return False
    if not par_jnt or not cmds.objExists(par_jnt):
        cmds.warning("拾取骨骼 '{}' 没有父关节，无法写入".format(picked))
        return False
    if not is_in_skin(mesh_name, picked):
        cmds.warning("拾取骨骼 '{}' 不在 '{}' 的蒙皮内".format(picked, mesh_name))
        return False
    if not is_in_skin(mesh_name, par_jnt):
        cmds.warning("父关节 '{}' 不在 '{}' 的蒙皮内".format(par_jnt, mesh_name))
        return False

    fwd_locs = ctx.group_mgr.get_joints_from_active('fwd')
    bwd_locs = ctx.group_mgr.get_joints_from_active('bwd')

    side_locs = None
    if len(fwd_locs) >= 2 and len(bwd_locs) >= 2:
        side_locs = (fwd_locs, bwd_locs)
        log.info("使用 bind pose locs: fwd={}, bwd={}".format(
            len(fwd_locs), len(bwd_locs)))
    elif fwd_locs or bwd_locs:
        cmds.warning("边界模式两侧 locator 不完整，取消计算")
        return False
    else:
        # This pipeline belongs to the curve-boundary tab. Drawing the
        # ridge is not authorization to paint an unrelated radius/slab.
        log.info("[曲线边界 V17.3] 等待 FWD/BWD；仅刷新当前颜色，不计算或写入回退权重")
        current = _read_original_weights(mesh_name, picked)
        return ctx.color_display.apply(mesh_name, current) if current is not None else False

    # joints 仍然只包含需要计算的子骨骼。
    # parent_joint 单独传递，用于读取父骨骼逐顶点真实权重上限。
    log.info("[诊断] 即将调用 WeightCalculator.compute: joints={}, picked={}, parent_joint={}".format(
        joints, picked, par_jnt))
    log.info("[诊断] 曲线参数: closed={}, xs={}, ys={}, fwd_locs={}, bwd_locs={}".format(
        ctx.active_closed,
        len(ctx.last_xs) if ctx.last_xs is not None else None,
        len(ctx.last_ys) if ctx.last_ys is not None else None,
        len(fwd_locs), len(bwd_locs)))
        
    try:
        curve_names = _active_boundary_curve_names(ctx) if side_locs is not None else None
        result = ctx.weight_calc.compute(
            mesh_name, joints, ctx.last_xs, ctx.last_ys,
            closed=ctx.active_closed,
            verbose=True,
            side_locs=side_locs,
            rest_surface=ctx.rest_surface,
            return_mask=True,
            parent_joint=par_jnt,
            child_joint=picked,
            curve_names=curve_names,
        )
    except Exception as e:
        log.warn("权重计算异常: {}".format(e))
        log.warn(traceback.format_exc())
        cmds.warning("权重计算异常，详细原因请查看 Script Editor / 日志")
        return False
        
    log.info("[诊断] WeightCalculator 返回类型: {}".format(type(result).__name__))
    if isinstance(result, tuple):
        log.info("[诊断] WeightCalculator 返回 tuple 长度: {}".format(len(result)))

    if result is None:
        cmds.warning("权重计算失败")
        return False

    if isinstance(result, tuple):
        weights, valid_mask = result
    else:
        weights = result
        valid_mask = None

    if weights is None:
        cmds.warning("权重计算失败")
        return False

    # Track only successful writes. Restoring an old region uses its captured
    # baseline, not an unbounded projection mask or unconditional zeros.
    write_mask = valid_mask
    history_key = None
    baseline = None
    if valid_mask is not None:
        original = _read_original_weights(mesh_name, picked)
        original_parent = _read_original_weights(mesh_name, par_jnt)
        if original is None or original_parent is None:
            cmds.warning("无法读取范围外保护所需的父子原权重，取消写入")
            return False
        if not hasattr(ctx, '_boundary_write_history'):
            ctx._boundary_write_history = {}
        def identity(node):
            return tuple(cmds.ls(node, uuid=True) or [node])
        history_key = (identity(mesh_name), identity(picked), identity(par_jnt),
                       ctx.group_mgr.active_set,
                       hashlib.blake2b(np.ascontiguousarray(ctx.rest_surface.core.faces).tobytes(),
                                       digest_size=16).digest())
        previous = ctx._boundary_write_history.get(history_key)
        weights, write_mask, baseline = prepare_write(
            previous, valid_mask, weights, original, original_parent)
        weights = np.where(write_mask, weights, original)

    try:
        _wa = np.asarray(weights, dtype=float)
        log.info("[诊断] 写入前 weights: shape={}, min={:.6f}, max={:.6f}, nonzero={}".format(
            _wa.shape, float(np.nanmin(_wa)), float(np.nanmax(_wa)), int(np.count_nonzero(_wa))))
    except Exception as _e:
        log.warn("[诊断] 无法统计待写权重: {}".format(_e))

    _log_skin_total_stats(mesh_name, "写入前")

    cmds.undoInfo(openChunk=True)
    try:
        ok, w_real = paint_pair(mesh_name, picked, par_jnt, weights, valid_mask=write_mask)
    except Exception as e:
        cmds.undoInfo(closeChunk=True)
        if write_mask is None:
            _undo_silently()
        cmds.warning("权重写入异常: {}".format(e))
        log.warn(traceback.format_exc())
        return False
    cmds.undoInfo(closeChunk=True)

    if not ok:
        cmds.warning("权重写入失败；请检查固定/锁定 influence 是否已经超过 Total=1")
        return False

    if history_key is not None:
        actual_parent = _read_original_weights(mesh_name, par_jnt)
        if actual_parent is not None:
            ctx._boundary_write_history[history_key] = committed_state(
                valid_mask, baseline, w_real, actual_parent)
    _log_skin_total_stats(mesh_name, "写入后")
    return ctx.color_display.apply(mesh_name, w_real)


def delete_color_set_and_clean_history(ctx, mesh_name,
                                       color_set_name="geoWeightVis"):
    display = ctx.color_display
    saved_name = display.color_set_name
    try:
        display.color_set_name = color_set_name
        return display.remove(mesh_name)
    finally:
        display.color_set_name = saved_name         
