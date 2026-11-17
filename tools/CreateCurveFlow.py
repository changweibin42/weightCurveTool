# -*- coding: utf-8 -*-
"""CreateCurveFlow —— 创建曲线流程

在模型表面连续点击创建 loc 骨骼；点回首节点闭合，或按 Q 保留开放曲线并退出。
"""
import maya.cmds as cmds

from ..Logger import log
from .JointSurfaceBuilder import JointSurfaceBuilder
from .DualCurveBuilder import DualCurveBuilder
from .JointState import lock_joint
from .SurfaceBinder import bind_joint_to_mesh
from .WeightColorPipeline import update_combined_vertex_colors
from .GroupManager import sanitize_name
from .MayaSelectors import _loc_sort_key
from . import ColorScheme


# ============================================================================
# 流程参数
# ============================================================================

JOINT_RADIUS_AUTO   = True
JOINT_TARGET_RADIUS = 1.0
JOINT_RADIUS_RATIO  = 0.02
JOINT_RADIUS_MIN    = 0.3
JOINT_RADIUS_MAX    = 2.0

MIN_NODES_TO_CLOSE  = 4
HIT_MODE            = "joint_radius"
SCREEN_THRESHOLD    = 30
WORLD_THRESHOLD     = 0.15


# ============================================================================
# 内部工具
# ============================================================================

def _auto_joint_radius(mesh_name, target=JOINT_TARGET_RADIUS):
    try:
        bbox = cmds.exactWorldBoundingBox(mesh_name)
    except Exception:
        return target
    size = max(bbox[3] - bbox[0],
               bbox[4] - bbox[1],
               bbox[5] - bbox[2])
    if size < 1e-6:
        return target
    return max(JOINT_RADIUS_MIN,
               min(JOINT_RADIUS_MAX, size * JOINT_RADIUS_RATIO))


# ============================================================================
# 入口
# ============================================================================

def start_continuous_surface_locator(mesh_name, direction_joint, ctx):
    if not mesh_name or not mesh_name.strip():
        log.error("模型名称为空")
        return
    if not cmds.objExists(mesh_name):
        log.error(f"模型 '{mesh_name}' 不存在")
        return
    shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
    if not shapes:
        log.error(f"'{mesh_name}' 不是多边形网格")
        return
    if not direction_joint or not cmds.objExists(direction_joint):
        log.error(f"方向骨骼 '{direction_joint}' 不存在")
        return
    if cmds.nodeType(direction_joint) != 'joint':
        log.error(f"'{direction_joint}' 不是关节")
        return

    ctx.continuous_mesh = mesh_name
    ctx.active_closed = False

    ctx.group_mgr.create_new_set()

    if ctx.curve_builder is None:
        ctx.curve_builder = DualCurveBuilder(rest_surface=ctx.rest_surface)
    else:
        ctx.curve_builder.rest_surface = ctx.rest_surface

    if ctx.surface_tool is not None:
        ctx.surface_tool.deactivate()
    ctx.surface_tool = JointSurfaceBuilder()

    try:
        cmds.select(mesh_name, replace=True)
    except Exception:
        pass

    set_num = ctx.group_mgr._get_next_set_number() - 1
    prefix_base = (
        f"{sanitize_name(mesh_name)}_"
        f"{sanitize_name(direction_joint)}_"
        f"{set_num:03d}"
    )

    radius = _auto_joint_radius(mesh_name) if JOINT_RADIUS_AUTO else JOINT_TARGET_RADIUS

    def on_node_created(mesh_name, joint_name):
        idx = len(ctx.group_mgr.get_joints_from_active('main')) + 1
        new_name = f"loc_{prefix_base}_{idx:03d}"
        try:
            actual = cmds.rename(joint_name, new_name)
        except Exception as e:
            log.warn(f"joint 重命名失败: {e}")
            actual = joint_name

        ctx.group_mgr.move_joint_to_active(actual, kind='main')
        lock_joint(actual)

        ColorScheme.colorize_loc(actual)

        bind_joint_to_mesh(actual, mesh_name, verbose=True,
                           rest_surface=ctx.rest_surface)

        group_joints = ctx.group_mgr.get_joints_from_active('main')

        if len(group_joints) >= 2:
            group_joints = sorted(group_joints, key=_loc_sort_key)
            try:
                ctx.curve_builder.create(mesh_name, group_joints,
                                         closed=False, verbose=True)
                ctx.group_mgr.move_curves_to_active(mesh_name, kind='main')
            except Exception as e:
                log.warn(f"实时重建曲线失败: {e}")

        update_combined_vertex_colors(ctx, mesh_name, closed=False)

        try:
            cmds.select(mesh_name, replace=True)
        except Exception:
            pass

        return actual

    def on_finish(mesh_name, joints, closed):
        ctx.active_closed = bool(closed)
        log.info("=" * 60)
        log.info(f"收尾 ({'闭合' if closed else '开放'})")

        group_joints = ctx.group_mgr.get_joints_from_active('main')
        if len(group_joints) < 2:
            log.warn("有效 joint 不足 2 个")
            deactivate_surface_locator(ctx)
            return

        group_joints = sorted(group_joints, key=_loc_sort_key)

        if closed:
            try:
                ctx.curve_builder.create(mesh_name, group_joints,
                                         closed=True, verbose=True)
                ctx.group_mgr.move_curves_to_active(mesh_name, kind='main')
            except Exception as e:
                log.warn(f"生成闭合曲线失败: {e}")

        update_combined_vertex_colors(ctx, mesh_name, closed=closed)
        # ★ 创建后刷新曲线显示（rest pose → 当前 pose）
        try:
            from ..core import refresh_display_curves
            refresh_display_curves()
        except Exception:
            pass        
        log.info("=" * 60)

        deactivate_surface_locator(ctx)

        try:
            cmds.select(mesh_name, replace=True)
        except Exception:
            pass

    ctx.surface_tool.activate(
        mesh_name=mesh_name,
        continuous=True,
        on_node_created=on_node_created,
        on_finish=on_finish,
        min_nodes_to_close=MIN_NODES_TO_CLOSE,
        joint_radius=radius,
        hit_mode=HIT_MODE,
        screen_threshold=SCREEN_THRESHOLD,
        world_threshold=WORLD_THRESHOLD,
    )


def deactivate_surface_locator(ctx):
    if ctx.surface_tool is not None:
        ctx.surface_tool.deactivate()
        ctx.surface_tool = None
    try:
        cmds.setToolTo('selectSuperContext')
    except Exception:
        pass


# ============================================================================
# 从 set 重建曲线
# ============================================================================

def rebuild_curves_from_set(set_name, kind, closed, ctx):
    group_joints = ctx.group_mgr.get_joints_from_set(set_name, kind=kind)
    if len(group_joints) < 2:
        log.warn(f"{kind} joints 数不足: {len(group_joints)}")
        return False

    mesh_name = ctx.continuous_mesh
    if not mesh_name or not cmds.objExists(mesh_name):
        log.warn("无法确定 mesh（请先通过 UI 创建一次）")
        return False

    log.info(f"从组重建曲线 ({len(group_joints)} joints, "
             f"kind={kind}, closed={closed})")

    ctx.active_closed = bool(closed)

    if ctx.curve_builder is None:
        ctx.curve_builder = DualCurveBuilder(rest_surface=ctx.rest_surface)
    else:
        ctx.curve_builder.rest_surface = ctx.rest_surface

    group_joints = sorted(group_joints, key=_loc_sort_key)

    try:
        prefix = mesh_name if kind == 'main' else f"{mesh_name}_{kind}"
        result = ctx.curve_builder.create(mesh_name, group_joints, closed=closed,
                                          verbose=True, name_prefix=prefix)
        if result:
            ctx.group_mgr.move_curves_to_set(set_name, mesh_name, kind=kind)
        if kind == 'main':
            update_combined_vertex_colors(ctx, mesh_name, closed=closed,
                                          joints=group_joints)
        return bool(result)
    except Exception as e:
        log.warn(f"重建失败: {e}")
        return False
        
    if kind == 'main':
        update_combined_vertex_colors(ctx, mesh_name, closed=closed,
                                      joints=group_joints)
    # ★ 刷新显示
    try:
        from ..core import refresh_display_curves
        refresh_display_curves()
    except Exception:
        pass
    return bool(result)        