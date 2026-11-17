# -*- coding: utf-8 -*-
"""BoundaryFlow —— 生成边界模式

在模型表面点击一次，点击侧作为 fwd 侧；
点击位置到脊线的距离决定滑动量；
沿表面两侧滑出 fwd/bwd 曲线 + 骨骼。
"""
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
import numpy as np

from ..Logger import log
from .JointState import lock_joint
from .SurfaceBinder import bind_joint_to_mesh
from .WeightColorPipeline import update_combined_vertex_colors
from .MayaSelectors import find_main_locs
from .DualCurveBuilder import DualCurveBuilder


# ============================================================================
# 主类
# ============================================================================

class BoundaryPickTool(object):
    CTX = "BoundaryPickCtx"

    def __init__(self, ctx):
        self.ctx = ctx
        self._active = False
        self._mesh_name = None
        self._press_pos = None
        self._ridge_pts = None

    # ==================== 启动 / 停止 ====================

    def activate(self, mesh_name):
        if not mesh_name or not mesh_name.strip():
            log.error("模型名称为空")
            return False
        if not cmds.objExists(mesh_name):
            log.error(f"模型 '{mesh_name}' 不存在")
            return False

        ridge_name = f"{mesh_name}_ring_laplacian"
        if not cmds.objExists(ridge_name):
            log.error(f"脊线 '{ridge_name}' 不存在")
            return False

        shapes = cmds.listRelatives(ridge_name, shapes=True, type='nurbsCurve')
        if not shapes:
            log.error("脊线 shape 不存在")
            return False
        cs = shapes[0]
        pts = []
        for i in range(150):
            t = i / 149.0
            pts.append(cmds.pointOnCurve(cs, parameter=t,
                                         position=True, turnOnPercentage=True))

        self._mesh_name = mesh_name
        self._press_pos = None
        self._ridge_pts = np.array(pts, dtype=float)

        locs = find_main_locs(mesh_name)
        log.info(f"生成边界模式：{mesh_name}")
        log.info(f"  找到 {len(locs)} 个主 loc")
        for loc in locs:
            log.info(f"    {loc}")
        log.info("请在模型表面点击（点击侧 = fwd 侧）")

        try:
            cmds.select(mesh_name, replace=True)
        except Exception:
            pass

        if cmds.draggerContext(self.CTX, exists=True):
            cmds.deleteUI(self.CTX)
        cmds.draggerContext(self.CTX,
                            pressCommand=self._on_press,
                            releaseCommand=self._on_release,
                            name=self.CTX,
                            cursor='crossHair')
        cmds.setToolTo(self.CTX)

        self._active = True
        return True

    def deactivate(self):
        if cmds.draggerContext(self.CTX, exists=True):
            cmds.deleteUI(self.CTX)
        try:
            cmds.setToolTo('selectSuperContext')
        except Exception:
            pass

        self._active = False
        self._mesh_name = None
        self._press_pos = None
        self._ridge_pts = None
        log.info("生成边界模式已停止")

    # ==================== 点击回调 ====================

    def _on_press(self, *args):
        try:
            vpX, vpY, _ = cmds.draggerContext(self.CTX, q=True, anchorPoint=True)
            self._press_pos = (int(vpX), int(vpY))
        except Exception:
            self._press_pos = None

    def _on_release(self, *args):
        if self._press_pos is None:
            return

        vpX, vpY = self._press_pos
        self._press_pos = None

        mesh_name = self._mesh_name
        shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
        if not shapes:
            return
        sel = om.MSelectionList(); sel.add(shapes[0])
        fn = om.MFnMesh(sel.getDagPath(0))

        view = omui.M3dView.active3dView()
        pos = om.MPoint(); direction = om.MVector()
        view.viewToWorld(vpX, vpY, pos, direction)

        hit = fn.closestIntersection(
            om.MFloatPoint(pos), om.MFloatVector(direction),
            om.MSpace.kWorld, 99999.0, False)
        if not hit:
            log.warn("未命中模型")
            return

        hit_pt = np.array([hit[0][0], hit[0][1], hit[0][2]])
        diffs = self._ridge_pts - hit_pt
        d = np.linalg.norm(diffs, axis=1)
        idx = int(np.argmin(d))
        distance = float(d[idx])
        log.info(f"命中 -> slide 距离 = {distance:.4f}")

        if distance < 1e-4:
            log.info("  距离过小，忽略")
            return

        self.deactivate()
        self._do_build(mesh_name, distance, click_pos=hit_pt)

    # ==================== 生成 ====================

    def _do_build(self, mesh_name, distance, click_pos=None):
        locs = find_main_locs(mesh_name)

        log.info("开始生成边界...")

        for side in ('fwd', 'bwd'):
            gau_crv = f"{mesh_name}_{side}_ring_gaussian"
            if cmds.objExists(gau_crv):
                cmds.delete(gau_crv)
                log.info(f"  已删除旧的 {gau_crv}")

        # 1. slide_builder.build 传 rest_surface
        result = self.ctx.slide_builder.build(
            mesh_name, f"{mesh_name}_ring_laplacian",
            distance, locs=locs, click_pos=click_pos, verbose=True,
            make_curves=False,
            rest_surface=self.ctx.rest_surface)

        if not result:
            log.warn("生成失败")
            return

        # 2. bind_joint_to_mesh 传 rest_surface（两个循环都改）
        for jnt in result['fwd_joints']:
            if cmds.objExists(jnt):
                self.ctx.group_mgr.move_joint_to_active(jnt, kind='fwd')
                lock_joint(jnt)
                bind_joint_to_mesh(jnt, mesh_name, verbose=False,
                                   rest_surface=self.ctx.rest_surface)
        for jnt in result['bwd_joints']:
            if cmds.objExists(jnt):
                self.ctx.group_mgr.move_joint_to_active(jnt, kind='bwd')
                lock_joint(jnt)
                bind_joint_to_mesh(jnt, mesh_name, verbose=False,
                                   rest_surface=self.ctx.rest_surface)

        closed = bool(result.get('closed', False))
        fwd_jnts = result['fwd_joints']
        bwd_jnts = result['bwd_joints']

        if len(fwd_jnts) >= 2 or len(bwd_jnts) >= 2:
            if self.ctx.curve_builder is None:
                self.ctx.curve_builder = DualCurveBuilder(rest_surface=self.ctx.rest_surface)
            elif self.ctx.curve_builder.rest_surface is not self.ctx.rest_surface:
                self.ctx.curve_builder.rest_surface = self.ctx.rest_surface

            for kind, jnts in (('fwd', fwd_jnts), ('bwd', bwd_jnts)):
                if len(jnts) < 2:
                    continue
                try:
                    # Keep every slid EP, not only the sparse boundary locs.
                    self.ctx.curve_builder.set_boundary_correspondence(
                        mesh_name, jnts, result['ep_'+kind],
                        result['anchor_ep_indices'], closed)
                    self.ctx.curve_builder.create(
                        mesh_name, jnts, closed=closed,
                        verbose=False, name_prefix=f"{mesh_name}_{kind}")
                    self.ctx.group_mgr.move_curves_to_active(mesh_name, kind=kind)
                    log.ok(f"已重建 {kind} 曲线")
                except Exception as e:
                    log.warn(f"重建 {kind} 曲线失败: {e}")
                    return  # Do not compute weights from partial/old boundaries.

        log.ok(f"骨骼: {len(fwd_jnts)} fwd + {len(bwd_jnts)} bwd 已归入对应组并锁定")

        try:
            cmds.select(mesh_name, replace=True)
        except Exception:
            pass
            
        # ★ 刷新曲线显示
        try:
            from ..core import refresh_display_curves
            refresh_display_curves()
        except Exception:
            pass
            
        # 3. 末尾调 refresh_display_all（在 log.ok("边界生成成功") 之前）
        if self.ctx.rest_surface is not None:
            try:
                from . import CurveLocalPoints
                n = CurveLocalPoints.refresh_display_all(self.ctx.rest_surface,
                                                         prefix_filter=mesh_name)
                if n:
                    log.info(f"刷新 {n} 条曲线显示")
            except Exception as e:
                log.warn(f"刷新曲线显示失败: {e}")            
            
            
        # Compute only after final display mapping: membership must use
        # precisely the curves that remain visible in the viewport.
        main_locs = find_main_locs(mesh_name)
        ok = bool(main_locs) and update_combined_vertex_colors(
            self.ctx, mesh_name, joints=main_locs)
        if ok:
            log.ok("边界与权重更新成功")
        else:
            log.warn("边界已生成，但权重未更新；请查看具体错误，不能按旧颜色判断本次结果")


# ============================================================================
# 模块级入口
# ============================================================================

_boundary_tool = None


def start_boundary_pick_mode(mesh_name, ctx):
    global _boundary_tool
    if _boundary_tool is None:
        _boundary_tool = BoundaryPickTool(ctx)
    else:
        _boundary_tool.ctx = ctx
    _boundary_tool.activate(mesh_name)


def stop_boundary_pick_mode():
    global _boundary_tool
    if _boundary_tool is not None:
        _boundary_tool.deactivate()