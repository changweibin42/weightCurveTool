# -*- coding: utf-8 -*-
"""JointSurfaceBuilder —— 在模型表面点击创建独立 joint 的工具"""
import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
import numpy as np

from ..Logger import log


# ========================== 固定名字 ==========================

CTX_NAME = 'JointSurfaceBuilderCtx'


# ========================== 模块级 job 跟踪 ==========================

_JOB_ID = None    # 当前 ToolChanged scriptJob 的 id


# ========================== 模块级清理 ==========================

def cleanup_global_watchers():
    """
    清理所有可能的历史残留：
      · 本模块记录的 ToolChanged scriptJob
      · draggerContext（固定名字）
      · 当前工具切回 select
    """
    global _JOB_ID

    # 1) scriptJob（按 id 清）
    if _JOB_ID is not None:
        try:
            if cmds.scriptJob(exists=_JOB_ID):
                cmds.scriptJob(kill=_JOB_ID, force=True)
                log.info(f"已清理残留 scriptJob (id={_JOB_ID})")
        except Exception as e:
            log.warn(f"清理 scriptJob 失败: {e}")
        _JOB_ID = None

    # 2) draggerContext
    try:
        if cmds.draggerContext(CTX_NAME, exists=True):
            cmds.deleteUI(CTX_NAME)
            log.info(f"已清理残留 draggerContext: {CTX_NAME}")
    except Exception as e:
        log.warn(f"清理 draggerContext 失败: {e}")

    # 3) 切回选择工具
    try:
        cur = cmds.currentCtx()
    except Exception:
        cur = None
    if cur == CTX_NAME:
        try:
            cmds.setToolTo('selectSuperContext')
        except Exception:
            pass


# ========================== 主类 ==========================

class JointSurfaceBuilder(object):
    """在模型表面点击创建独立 joint。

    命中判定用"射线-球距离"数学计算。
    """

    def __init__(self):
        self.ctx = CTX_NAME

        # 目标
        self.target_mesh = None
        self.mesh_shape = None

        # 状态
        self.joints = []
        self.continuous = True
        self.finished = False
        self.finished_closed = False

        # 回调
        self.on_node_created = None
        self.on_finish = None

        # 参数
        self.min_nodes_to_close = 4
        self.joint_radius = 1.0

        # 命中判定
        self.hit_mode = 'joint_radius'
        self.screen_threshold = 30
        self.world_threshold = 0.15

    # ==================== 启动 / 停止 ====================

    def activate(self, mesh_name=None, continuous=True,
                 on_node_created=None, on_finish=None,
                 min_nodes_to_close=4, joint_radius=1.0,
                 hit_mode='joint_radius',
                 screen_threshold=30,
                 world_threshold=0.15):
        # ★ 先做模块级清理
        cleanup_global_watchers()

        if not self._resolve_mesh(mesh_name):
            return False

        self.continuous = continuous
        self.on_node_created = on_node_created
        self.on_finish = on_finish
        self.min_nodes_to_close = int(min_nodes_to_close)
        self.joint_radius = float(joint_radius)
        self.hit_mode = hit_mode
        self.screen_threshold = float(screen_threshold)
        self.world_threshold = float(world_threshold)

        self.joints = []
        self.finished = False
        self.finished_closed = False

        # 创建 draggerContext
        if cmds.draggerContext(self.ctx, exists=True):
            cmds.deleteUI(self.ctx)
        cmds.draggerContext(
            self.ctx,
            pressCommand=self._on_click,
            name=self.ctx,
            cursor='crossHair',
        )
        cmds.setToolTo(self.ctx)

        # 装 scriptJob
        self._install_tool_watcher()

        mode = "连续" if continuous else "单次"
        log.info(f"JointSurfaceBuilder 已启动（{mode}）")
        log.info(f"  网格: {self.target_mesh}")
        log.info(f"  joint 半径: {self.joint_radius:.4f}")
        log.info(f"  命中模式: {self.hit_mode}")
        log.info(f"  至少 {self.min_nodes_to_close} 个节点后可闭合")
        return True

    def deactivate(self):
        if self.finished:
            return
        self.finished = True
        self._cleanup_ui()
        log.info("JointSurfaceBuilder 已停止")

    def get_result(self):
        return self.target_mesh, list(self.joints), self.finished_closed

    def get_joints(self):
        return list(self.joints)

    # ==================== mesh 解析 ====================

    def _resolve_mesh(self, mesh_name):
        if mesh_name is None:
            sel = cmds.ls(selection=True, type='transform')
            if not sel:
                cmds.warning("请先选择一个多边形模型，或传入 mesh_name")
                return False
            mesh_name = sel[0]

        if not cmds.objExists(mesh_name):
            cmds.warning(f"模型 '{mesh_name}' 不存在")
            return False

        shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
        if shapes:
            self.target_mesh = mesh_name
            self.mesh_shape = shapes[0]
            return True

        if cmds.nodeType(mesh_name) == 'mesh':
            pars = cmds.listRelatives(mesh_name, parent=True) or []
            self.target_mesh = pars[0] if pars else mesh_name
            self.mesh_shape = mesh_name
            return True

        cmds.warning(f"'{mesh_name}' 不是多边形网格")
        return False

    # ==================== 点击处理 ====================

    def _on_click(self, *args):
        if self.finished or not self.target_mesh:
            return

        try:
            vp_x, vp_y, _ = cmds.draggerContext(self.ctx, q=True, anchorPoint=True)
        except Exception as e:
            log.warn(f"拿点击位置失败: {e}")
            return

        n = len(self.joints)
        thr = self.min_nodes_to_close

        # 判定闭合
        if n >= thr:
            hit, _ = self._hits_first(vp_x, vp_y)
            if hit:
                log.info("命中首节点球，闭合退出")
                self._finish(closed=True)
                return

        # 跟 mesh 求交
        pos = self._ray_to_surface(vp_x, vp_y)
        if pos is None:
            log.warn("点击位置不在模型表面")
            return

        for jnt in self.joints:
            if not cmds.objExists(jnt):
                continue
            jp = np.array(cmds.xform(jnt, q=True, ws=True, t=True))
            if np.linalg.norm(jp - np.array(pos)) < 1e-4:
                return

        idx = n + 1
        cmds.select(clear=True)
        new_jnt = cmds.joint(
            name=f"joint_{idx}",
            position=pos,
            radius=self.joint_radius,
        )
        cmds.select(clear=True)

        self.joints.append(new_jnt)

        if self.on_node_created:
            try:
                result = self.on_node_created(self.target_mesh, new_jnt)
                if isinstance(result, str) and result and cmds.objExists(result):
                    self.joints[-1] = result
            except Exception as e:
                log.warn(f"on_node_created 回调出错: {e}")

        if not self.continuous:
            self._finish(closed=False)

    # ==================== 命中判定 ====================

    def _hits_first(self, vp_x, vp_y):
        if not self.joints:
            return False, "无首节点"

        first = self.joints[0]

        if not cmds.objExists(first):
            short = first.split("|")[-1] if "|" in first else first
            matches = cmds.ls(short, type='joint') or []
            if matches:
                first = matches[0]
                self.joints[0] = first
            else:
                return False, f"首节点不存在 ({first})"

        fp = cmds.xform(first, q=True, ws=True, t=True)

        if self.hit_mode == 'screen':
            d = self._screen_distance(vp_x, vp_y, fp)
            if d is None:
                return False, "屏幕投影失败"
            return d < self.screen_threshold, f"屏幕距离 {d:.1f}px"

        if self.hit_mode == 'joint_radius':
            try:
                r = cmds.getAttr(f"{first}.radius")
                if r <= 1e-6:
                    r = self.joint_radius
            except Exception:
                r = self.joint_radius
        else:
            r = self.world_threshold

        try:
            view = omui.M3dView.active3dView()
            ray_pos = om.MPoint()
            ray_dir = om.MVector()
            view.viewToWorld(int(vp_x), int(vp_y), ray_pos, ray_dir)

            rp = np.array([ray_pos.x, ray_pos.y, ray_pos.z])
            rd = np.array([ray_dir.x, ray_dir.y, ray_dir.z])
            rd_len = np.linalg.norm(rd)
            if rd_len < 1e-10:
                return False, "射线方向无效"
            rd = rd / rd_len

            oc = rp - np.array(fp)
            t = -float(np.dot(oc, rd))
            closest = oc + t * rd if t > 0 else oc
            dist = float(np.linalg.norm(closest))

            hit = dist < r
            return hit, f"射线-球距 {dist:.4f} (阈值 {r:.4f}, {self.hit_mode})"
        except Exception as e:
            return False, f"射线计算失败: {e}"

    def _screen_distance(self, vp_x, vp_y, world_pos):
        try:
            view = omui.M3dView.active3dView()
            pt = view.worldToView(om.MPoint(world_pos[0], world_pos[1], world_pos[2]))
            h = view.portHeight()
            sx = pt[0]
            sy = h - pt[1]
            dx = vp_x - sx
            dy = vp_y - sy
            return (dx * dx + dy * dy) ** 0.5
        except Exception:
            return None

    # ==================== 射线求交 ====================

    def _ray_to_surface(self, vp_x, vp_y):
        try:
            pos = om.MPoint()
            direction = om.MVector()
            view = omui.M3dView.active3dView()
            view.viewToWorld(int(vp_x), int(vp_y), pos, direction)
            sel = om.MSelectionList()
            sel.add(self.mesh_shape)
            fn = om.MFnMesh(sel.getDagPath(0))
            hit = fn.closestIntersection(
                om.MFloatPoint(pos), om.MFloatVector(direction),
                om.MSpace.kWorld, 99999.0, False)
            if hit:
                h = hit[0]
                return (h[0], h[1], h[2])
        except Exception as e:
            log.warn(f"射线检测失败: {e}")
        return None

    # ==================== 收尾 ====================

    def _finish(self, closed=False):
        if self.finished:
            return
        self.finished = True
        self.finished_closed = closed

        joints_snapshot = list(self.joints)
        mesh_snapshot = self.target_mesh

        self._cleanup_ui()

        tag = "闭合" if closed else "开放（Q 键）"
        log.info("=" * 60)
        log.ok(f"JointSurfaceBuilder 完成（{tag}），共 {len(joints_snapshot)} 个节点")
        log.info("=" * 60)

        if self.on_finish and len(joints_snapshot) >= 2:
            try:
                self.on_finish(mesh_snapshot, joints_snapshot, closed)
            except Exception as e:
                log.warn(f"on_finish 回调出错: {e}")

    def _cleanup_ui(self):
        """收尾/停止时清理 UI 和 scriptJob"""
        self._kill_tool_watcher()
        if cmds.draggerContext(self.ctx, exists=True):
            try:
                cmds.deleteUI(self.ctx)
            except Exception:
                pass
        try:
            cmds.setToolTo('selectSuperContext')
        except Exception:
            pass

    # ==================== scriptJob ====================

    def _install_tool_watcher(self):
        """装 ToolChanged 监听（用返回值跟踪 job id）"""
        global _JOB_ID
        self._kill_tool_watcher()
        try:
            _JOB_ID = cmds.scriptJob(
                event=['ToolChanged', self._on_tool_changed],
                protected=False,
            )
            log.ok(f"scriptJob 已装 (id={_JOB_ID})")
        except Exception as e:
            log.warn(f"安装 ToolChanged 监听失败: {e}")

    def _kill_tool_watcher(self):
        """按 id 清理"""
        global _JOB_ID
        if _JOB_ID is not None:
            try:
                if cmds.scriptJob(exists=_JOB_ID):
                    cmds.scriptJob(kill=_JOB_ID, force=True)
            except Exception:
                pass
            _JOB_ID = None

    def _on_tool_changed(self):
        cmds.evalDeferred(self._check_tool_state)

    def _check_tool_state(self):
        if self.finished:
            return
        if not cmds.draggerContext(self.ctx, exists=True):
            return
        try:
            cur = cmds.currentCtx()
        except Exception:
            cur = None
        if cur != self.ctx:
            log.info("检测到工具切换（Q 键），保留退出")
            self._finish(closed=False)