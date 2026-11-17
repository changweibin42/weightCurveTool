# -*- coding: utf-8 -*-
"""ModifyCurveTool —— 修改曲线模式

★ 拖动逻辑（uvw 驱动）：
  · 鼠标屏幕坐标 → 射线 ∩ 当前 pose mesh → 命中点 P_cur
  · 用 P_cur 更新被拖 loc 的 wct_*（ids + uvw）
  · loc 世界位置 = 当前 pose 顶点 + ids + uvw 插值

★ 性能：拖动中缓存 worldMesh 顶点
  · press 时构造一次 MFnMesh + 全量顶点数组
  · drag 时直接用缓存（mesh 没变），不重新求值
  · release 时清缓存

★ 权重与视觉的依赖链（无循环）：
  wct_*（拓扑位置，拖动时更新）
    ├─→ 权重计算（bind pose 顶点 + wct_*）→ skinCluster → mesh 变形
    └─→ loc 世界位置（当前 pose 顶点 + wct_*）→ 曲线生成
"""
import maya.cmds as cmds
import maya.api.OpenMayaUI as omui
import maya.api.OpenMaya as om
import numpy as np

from ..Logger import log
from .DualCurveBuilder import DualCurveBuilder
from .JointState import lock_joint, unlock_joint, lock_all_locs
from .SurfaceBinder import (
    _find_best_triangle,
    _ensure_bind_attrs,
    rebind_all_joints,
    infer_mesh_from_joint,
)
from .WeightColorPipeline import update_combined_vertex_colors
from .MayaSelectors import find_main_locs, _loc_sort_key
from .NativeCurvePreview import NativeCurvePreview, FrozenMapping


def _get_joint_display_scale():
    try:
        return float(cmds.jointDisplayScale(query=True))
    except Exception:
        return 1.0


def _set_pos_lock(jnt, lock):
    keyable = not lock
    for a in ('tx', 'ty', 'tz'):
        try:
            cmds.setAttr(f"{jnt}.{a}",
                         lock=bool(lock),
                         keyable=keyable,
                         channelBox=keyable)
        except Exception:
            pass


def _write_bind_attrs(jnt, mesh_name, ids, u, v, w):
    """写 jnt 的 7 个 wct_* 属性。"""
    try:
        cmds.setAttr(f"{jnt}.wct_mesh", mesh_name, type='string')
        cmds.setAttr(f"{jnt}.wct_id0", ids[0])
        cmds.setAttr(f"{jnt}.wct_id1", ids[1])
        cmds.setAttr(f"{jnt}.wct_id2", ids[2])
        cmds.setAttr(f"{jnt}.wct_u", u)
        cmds.setAttr(f"{jnt}.wct_v", v)
        cmds.setAttr(f"{jnt}.wct_w", w)
        return True
    except Exception as e:
        log.warn(f"写绑定属性失败: {e}")
        return False


class ModifyCurveTool(object):
    CTX = 'ModifyCurveToolCtx'
    MOVE_EPSILON = 1e-4

    def __init__(self, ctx):
        self.ctx = ctx

        self._active = False
        self._picked = False
        self._jnt = None
        self._p0_world = None
        self._drag_offset_cam = None
        self._plane_center = None
        self._jnts = None
        self._kind = None
        self._mesh_name = None
        self._undo_open = False

        self._native_preview = None
        self._frozen_mapping = None

        # ★ 拖动中缓存
        self._deformed_fn = None
        self._deformed_pts = None

    # ==================== 启动 / 停止 ====================

    def activate(self):
        self.deactivate()

        locs = cmds.ls("loc_*", type='joint') or []
        if not locs:
            log.warn("场景中没有可修改的骨骼（请先用「创建曲线」创建）")
            return False

        mesh_name = (self.ctx.continuous_mesh
                     or self.ctx.group_mgr.find_mesh_from_active())
        self._force_mesh_dirty(mesh_name)

        self._rebind_and_rebuild_all()

        lock_all_locs()

        if cmds.draggerContext(self.CTX, exists=True):
            try:
                cmds.draggerContext(
                    self.CTX, edit=True,
                    pressCommand=self._on_press,
                    dragCommand=self._on_drag,
                    releaseCommand=self._on_release,
                )
            except Exception as e:
                log.warn(f"编辑已有 draggerContext 失败: {e}")
                cmds.deleteUI(self.CTX)
                cmds.draggerContext(
                    self.CTX,
                    pressCommand=self._on_press,
                    dragCommand=self._on_drag,
                    releaseCommand=self._on_release,
                    name=self.CTX,
                    cursor='crossHair',
                )
        else:
            cmds.draggerContext(
                self.CTX,
                pressCommand=self._on_press,
                dragCommand=self._on_drag,
                releaseCommand=self._on_release,
                name=self.CTX,
                cursor='crossHair',
            )

        cmds.setToolTo(self.CTX)

        if mesh_name and cmds.objExists(mesh_name):
            try:
                cmds.select(mesh_name, replace=True)
            except Exception:
                pass

        self._active = True
        log.info("修改曲线：鼠标移到骨骼球上按住左键拖动（loc 贴模型表面移动）")
        return True

    def _stop_native_preview(self):
        preview, self._native_preview = self._native_preview, None
        self._frozen_mapping = None
        if preview is not None:
            try:
                preview.close()
            except Exception as exc:
                log.warn("原生曲线预览清理失败: {}".format(exc))
        self.ctx._wct_curve_drag_active = False

    def deactivate(self):
        self._stop_native_preview()
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        self._undo_open = False

        if self._jnt and cmds.objExists(self._jnt):
            lock_joint(self._jnt)

        try:
            if cmds.currentCtx() == self.CTX:
                cmds.setToolTo('selectSuperContext')
        except Exception:
            pass

        lock_all_locs()

        self._active = False
        self._picked = False
        self._jnt = None
        self._p0_world = None
        self._drag_offset_cam = None
        self._plane_center = None
        self._jnts = None
        self._kind = None
        self._mesh_name = None
        self._deformed_fn = None
        self._deformed_pts = None

    @staticmethod
    def _force_mesh_dirty(mesh_name):
        if not mesh_name or not cmds.objExists(mesh_name):
            return
        shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
        if not shapes:
            return
        try:
            cmds.dgdirty(shapes[0])
        except Exception:
            pass
        try:
            cmds.refresh(force=True)
        except Exception:
            pass

    def _ensure_curve_builder(self):
        if self.ctx.curve_builder is None:
            self.ctx.curve_builder = DualCurveBuilder(rest_surface=self.ctx.rest_surface)
        elif self.ctx.curve_builder.rest_surface is not self.ctx.rest_surface:
            self.ctx.curve_builder.rest_surface = self.ctx.rest_surface
        return self.ctx.curve_builder

    def _resolve_closed(self, mesh_name):
        closed = self.ctx.active_closed
        crv_name = f"{mesh_name}_ring_laplacian"
        if not cmds.objExists(crv_name):
            return closed
        try:
            form = cmds.getAttr(f"{crv_name}.form")
        except Exception:
            return closed
        if form in (1, 2):
            return True
        if form == 0:
            return False
        return closed

    def _curve_prefix(self, mesh_name, kind):
        if kind == 'main':
            return mesh_name
        return f"{mesh_name}_{kind}"

    def _rebind_and_rebuild_all(self):
        log.info("=" * 60)
        log.info("重贴所有 loc 到 mesh 表面...")

        mesh_name = (self.ctx.group_mgr.find_mesh_from_active()
                     or self.ctx.continuous_mesh)
        self._force_mesh_dirty(mesh_name)

        rebound = rebind_all_joints(verbose=True,
                                    fallback_mesh=self.ctx.continuous_mesh,
                                    rest_surface=self.ctx.rest_surface)
        if rebound == 0:
            log.warn("没有 loc 被重贴")
            log.info("=" * 60)
            return

        if not mesh_name or not cmds.objExists(mesh_name):
            log.warn("无法确定 mesh，跳过曲线重建")
            log.info("=" * 60)
            return

        builder = self._ensure_curve_builder()
        closed = self._resolve_closed(mesh_name)

        for kind in ('main', 'fwd', 'bwd'):
            jnts = self.ctx.group_mgr.get_joints_from_active(kind)
            if len(jnts) < 2:
                continue
            jnts = sorted(jnts, key=_loc_sort_key)
            prefix = self._curve_prefix(mesh_name, kind)
            try:
                builder.create(mesh_name, jnts, closed=closed,
                               verbose=False, name_prefix=prefix)
                self.ctx.group_mgr.move_curves_to_active(mesh_name, kind=kind)
                log.ok(f"已重建 {kind} 曲线")
            except Exception as e:
                log.warn(f"重建 {kind} 曲线失败: {e}")

        log.info("=" * 60)

    def _screen_to_world_plane(self, vp_x, vp_y, plane_center):
        try:
            view = omui.M3dView.active3dView()
            pos = om.MPoint(); direction = om.MVector()
            view.viewToWorld(int(vp_x), int(vp_y), pos, direction)

            ray_origin = np.array([pos.x, pos.y, pos.z], dtype=float)
            ray_dir = np.array([direction.x, direction.y, direction.z], dtype=float)
            ln = np.linalg.norm(ray_dir)
            if ln < 1e-10:
                return None
            ray_dir = ray_dir / ln

            cam_dag = view.getCamera()
            m = cam_dag.inclusiveMatrix()
            cam_fwd = -np.array([float(m[8]), float(m[9]), float(m[10])],
                                dtype=float)
            ln2 = np.linalg.norm(cam_fwd)
            if ln2 < 1e-10:
                return None
            cam_fwd = cam_fwd / ln2

            plane_center = np.asarray(plane_center, dtype=float)
            denom = float(np.dot(ray_dir, cam_fwd))
            if abs(denom) < 1e-9:
                return None
            t = float(np.dot(plane_center - ray_origin, cam_fwd)) / denom
            return ray_origin + ray_dir * t
        except Exception as e:
            log.warn(f"屏幕→世界平面失败: {e}")
            return None

    def _ray_hit_mesh_at_vp(self, vpX, vpY):
        if self._deformed_fn is None:
            return None
        try:
            view = omui.M3dView.active3dView()
            pos = om.MPoint(); direction = om.MVector()
            view.viewToWorld(int(vpX), int(vpY), pos, direction)
            hit = self._deformed_fn.closestIntersection(
                om.MFloatPoint(pos), om.MFloatVector(direction),
                om.MSpace.kWorld, 99999.0, False)
            if hit:
                hp = hit[0]
                return np.array([hp[0], hp[1], hp[2]], dtype=float)
        except Exception as e:
            log.warn(f"射线-mesh 求交失败: {e}")
        return None

    # ==================== 拖拽事件 ====================

    def _on_press(self, *args):
        if not self._active:
            return
        try:
            vpX, vpY, _ = cmds.draggerContext(self.CTX, q=True, anchorPoint=True)
        except Exception as e:
            log.warn(f"取按下位置失败: {e}")
            return

        jnt = self._pick_jnt_ray(int(vpX), int(vpY))
        if jnt is None:
            return

        unlock_joint(jnt)

        mesh_name = (infer_mesh_from_joint(jnt, fallback_mesh=self.ctx.continuous_mesh)
                     or self.ctx.continuous_mesh)
        self._force_mesh_dirty(mesh_name)

        # ★ press 时构造一次 MFnMesh + 缓存全量顶点
        self._deformed_fn = self._get_deformed_mesh_fn(mesh_name)
        self._deformed_pts = self._get_mesh_world_points(self._deformed_fn)
        self._mesh_name = mesh_name

        p0 = np.array(cmds.xform(jnt, q=True, ws=True, t=True))
        self._p0_world = p0
        self._plane_center = p0.copy()

        mouse_world_0 = self._screen_to_world_plane(
            int(vpX), int(vpY), self._plane_center)
        if mouse_world_0 is not None:
            self._drag_offset_cam = p0 - mouse_world_0
        else:
            self._drag_offset_cam = np.zeros(3, dtype=float)

        self._undo_open = False
        self._picked = True
        self._jnt = jnt

        self._kind = self.ctx.group_mgr.find_kind_by_joint(jnt) or 'main'
        self._jnts = self._get_same_kind_jnts(jnt)
        self._stop_native_preview()
        self._native_preview = NativeCurvePreview.begin(
            self.ctx, self._jnts, self._kind, mesh_name, self._deformed_pts)
        if self._native_preview is not None:
            try:
                self._frozen_mapping = FrozenMapping(self.ctx.rest_surface, self._deformed_pts)
            except Exception:
                self._stop_native_preview()

        log.info(f"拖动 {jnt}  [kind={self._kind}, 同类 {len(self._jnts)} 个]")

    @staticmethod
    def _get_mesh_world_points(fn):
        """一次拿 MFnMesh 的所有顶点世界坐标。"""
        if fn is None:
            return None
        try:
            pts = fn.getPoints(om.MSpace.kWorld)
        except Exception:
            return None
        n = len(pts)
        arr = np.empty((n, 3), dtype=float)
        for i in range(n):
            p = pts[i]
            arr[i, 0] = p.x
            arr[i, 1] = p.y
            arr[i, 2] = p.z
        return arr

    def _on_drag(self, *args):
        if not self._picked:
            return
        if not cmds.objExists(self._jnt):
            return

        try:
            vpX, vpY, _ = cmds.draggerContext(self.CTX, q=True, dragPoint=True)
        except Exception:
            return

        if not self._undo_open:
            cmds.undoInfo(openChunk=True)
            unlock_joint(self._jnt)
            self._undo_open = True

        # ★ 1. 射线∩当前 pose mesh
        P_cur = self._ray_hit_mesh_at_vp(int(vpX), int(vpY))
        if P_cur is None:
            if self._plane_center is None or self._drag_offset_cam is None:
                return
            mouse_world_t = self._screen_to_world_plane(
                int(vpX), int(vpY), self._plane_center)
            if mouse_world_t is None:
                return
            P_cur = mouse_world_t + self._drag_offset_cam

        rs = self.ctx.rest_surface

        # ★ 2. fallback：无 rest_surface，旧逻辑（当前 pose 直接求 uvw）
        if rs is None:
            if self._deformed_fn is None or self._deformed_pts is None:
                cmds.xform(self._jnt, ws=True,
                           t=(float(P_cur[0]), float(P_cur[1]), float(P_cur[2])))
                cmds.refresh(force=True)
                return
            try:
                pt = om.MPoint(float(P_cur[0]), float(P_cur[1]), float(P_cur[2]))
                closest, face_id = self._deformed_fn.getClosestPoint(
                    pt, om.MSpace.kWorld)
                c_arr = (closest.x, closest.y, closest.z)
            except Exception:
                return
            res = _find_best_triangle(self._deformed_fn, face_id, c_arr,
                                      self._deformed_pts)
            if res is None:
                return
            ids, u, v, w = res
            _ensure_bind_attrs(self._jnt)
            _write_bind_attrs(self._jnt, self._mesh_name, ids, u, v, w)
            i0, i1, i2 = ids
            if max(i0, i1, i2) >= len(self._deformed_pts):
                return
            new_pos = (u * self._deformed_pts[i0] +
                       v * self._deformed_pts[i1] +
                       w * self._deformed_pts[i2])
            _set_pos_lock(self._jnt, lock=False)
            try:
                cmds.xform(self._jnt, ws=True,
                           t=(float(new_pos[0]), float(new_pos[1]),
                              float(new_pos[2])))
            finally:
                _set_pos_lock(self._jnt, lock=True)
            cmds.refresh(force=True)
            return

        # ★ 3. 双空间：P_cur (current pose) → P_local (rest pose)
        P_arr = np.asarray([P_cur], dtype=float)
        P_local = (self._frozen_mapping or rs).display_to_local(P_arr)[0]

        faces, weights = rs.core.get_face_weights(P_local[None])
        face_id = int(faces[0])
        ids = rs.core.faces[face_id].tolist()
        uvw = weights[0]
        u, v, w = float(uvw[0]), float(uvw[1]), float(uvw[2])

        _ensure_bind_attrs(self._jnt)
        _write_bind_attrs(self._jnt, self._mesh_name, ids, u, v, w)

        rest_pos = rs.get_rest_position(ids, (u, v, w))
        display_pos = (self._frozen_mapping or rs).local_to_display(np.asarray([rest_pos], dtype=float))[0]

        _set_pos_lock(self._jnt, lock=False)
        try:
            cmds.xform(self._jnt, ws=True,
                       t=(float(display_pos[0]), float(display_pos[1]),
                          float(display_pos[2])))
        finally:
            _set_pos_lock(self._jnt, lock=True)
        cmds.refresh(force=True)

    def _on_release(self, *args):
        self._stop_native_preview()
        if not self._picked:
            return

        jnt = self._jnt
        p0 = self._p0_world
        self._picked = False
        self._jnt = None
        self._p0_world = None
        self._drag_offset_cam = None
        self._plane_center = None
        self._deformed_fn = None
        self._deformed_pts = None

        if jnt is None or not cmds.objExists(jnt):
            if self._undo_open:
                try:
                    cmds.undoInfo(closeChunk=True)
                except Exception:
                    pass
                self._undo_open = False
            return

        lock_joint(jnt)

        if self._undo_open:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                pass
            self._undo_open = False

        cur = np.array(cmds.xform(jnt, q=True, ws=True, t=True))
        moved = float(np.linalg.norm(cur - p0))
        if moved < self.MOVE_EPSILON and not getattr(self.ctx, "_wct_curve_weight_pending", False):
            return

        self.ctx._wct_curve_weight_pending = False
        try:
            self._rebuild(jnt)
        except Exception as e:
            log.warn(f"重建失败: {e}")

    def _rebuild(self, jnt):
        """拖动松手后：

        1. 重贴所有 loc 到 rest pose 表面（用户拖的 loc 的 wct_* 已在 _on_drag 更新）
        2. 重建所有曲线（用新 loc 的 wct_* 反推 rest pose anchors → 新 wcs_localPoints）
        3. 算权重（用新曲线的 wcs_localPoints → 8D loft / 8D IK）
        4. 刷新曲线显示（跟随当前 pose）
        """
        mesh_name = self._mesh_name
        if not mesh_name or not cmds.objExists(mesh_name):
            return

        builder = self._ensure_curve_builder()
        closed = self._resolve_closed(mesh_name)

        # ---- 步骤 1：重贴所有 loc ----
        self._force_mesh_dirty(mesh_name)
        try:
            rebound = rebind_all_joints(verbose=False,
                                        fallback_mesh=self.ctx.continuous_mesh,
                                        rest_surface=self.ctx.rest_surface)
            if rebound:
                log.info(f"重贴 {rebound} 个 loc")
        except Exception as e:
            log.warn(f"重贴 loc 失败: {e}")

        # ---- 步骤 2：重建所有曲线（更新 wcs_localPoints） ----
        for kind_iter in ('main', 'fwd', 'bwd'):
            jnts_iter = self.ctx.group_mgr.get_joints_from_active(kind_iter)
            if len(jnts_iter) < 2:
                continue
            jnts_iter = sorted(jnts_iter, key=_loc_sort_key)
            prefix = self._curve_prefix(mesh_name, kind_iter)
            try:
                builder.create(mesh_name, jnts_iter, closed=closed,
                               verbose=False, name_prefix=prefix)
                self.ctx.group_mgr.move_curves_to_active(mesh_name,
                                                         kind=kind_iter)
            except Exception as e:
                log.warn(f"重建 {kind_iter} 曲线失败: {e}")

        # ---- 步骤 4：刷新曲线显示 ----
        if self.ctx.rest_surface is not None:
            try:
                from . import CurveLocalPoints
                n = CurveLocalPoints.refresh_display_all(
                    self.ctx.rest_surface, prefix_filter=mesh_name)
                if n:
                    log.info(f"刷新 {n} 条曲线显示")
            except Exception as e:
                log.warn(f"刷新曲线显示失败: {e}")

        # ---- 步骤 3：算权重（用新曲线） ----
        main_locs = self.ctx.group_mgr.get_main_locs_from_joint_set(jnt)
        if not main_locs:
            main_locs = find_main_locs(mesh_name)

        if main_locs:
            self._force_mesh_dirty(mesh_name)
            try:
                ok = update_combined_vertex_colors(self.ctx, mesh_name,
                                                   closed=closed, joints=main_locs)
                if ok:
                    log.ok("已重绘权重")
                else:
                    log.warn("曲线已更新，但权重未重绘；请查看具体错误")
            except Exception as e:
                log.warn(f"权重重算失败: {e}")

        log.info("重贴与曲线刷新完成；权重更新状态见上方日志")

        try:
            cmds.select(mesh_name, replace=True)
        except Exception:
            pass

    def _get_deformed_mesh_fn(self, mesh_name):
        if not mesh_name or not cmds.objExists(mesh_name):
            return None
        shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
        if not shapes:
            return None
        shape_name = shapes[0]
        try:
            sel = om.MSelectionList(); sel.add(shape_name)
            dag = sel.getDagPath(0)
            dep = om.MFnDependencyNode(dag.node())
            plug = dep.findPlug("worldMesh", False).elementByLogicalIndex(0)
            return om.MFnMesh(plug.asMObject())
        except Exception:
            try:
                sel = om.MSelectionList(); sel.add(shape_name)
                return om.MFnMesh(sel.getDagPath(0))
            except Exception:
                return None

    def _get_same_kind_jnts(self, jnt):
        pars = cmds.listRelatives(jnt, parent=True, type='transform') or []
        if not pars:
            return [jnt]
        jg = pars[0]
        children = cmds.listRelatives(jg, children=True, type='joint') or []
        if not children:
            return [jnt]
        return sorted(children, key=_loc_sort_key)

    def _pick_jnt_ray(self, vpX, vpY):
        try:
            locs = cmds.ls("loc_*", type='joint') or []
            if not locs:
                return None

            view = omui.M3dView.active3dView()
            rp = om.MPoint(); rd = om.MVector()
            view.viewToWorld(int(vpX), int(vpY), rp, rd)

            p = np.array([rp.x, rp.y, rp.z])
            d = np.array([rd.x, rd.y, rd.z])
            dl = np.linalg.norm(d)
            if dl < 1e-10:
                return None
            d = d / dl

            positions = np.empty((len(locs), 3), dtype=float)
            radii = np.empty(len(locs), dtype=float)
            for i, jnt in enumerate(locs):
                try:
                    positions[i] = cmds.xform(jnt, q=True, ws=True, t=True)
                except Exception:
                    positions[i] = (0.0, 0.0, 0.0)
                try:
                    r = float(cmds.getAttr(f"{jnt}.radius"))
                    if r <= 1e-6:
                        r = 1.0
                except Exception:
                    r = 1.0
                radii[i] = r

            scale = _get_joint_display_scale() * 0.5
            radii = radii * scale

            oc = p[None, :] - positions
            t = -(oc @ d)
            closest = oc + t[:, None] * d
            dist = np.linalg.norm(closest, axis=1)

            valid = t > 0
            hit = valid & (dist <= radii)
            if not np.any(hit):
                return None

            t_masked = np.where(hit, t, np.inf)
            best_idx = int(np.argmin(t_masked))
            return locs[best_idx]
        except Exception as e:
            log.warn(f"拾取失败: {e}")
            return None


_modify_tool = None


def start_modify_curve_mode(ctx):
    global _modify_tool
    if _modify_tool is None:
        _modify_tool = ModifyCurveTool(ctx)
    else:
        _modify_tool.ctx = ctx
        _modify_tool.deactivate()
    _modify_tool.activate()


def stop_modify_curve_mode():
    global _modify_tool
    if _modify_tool is not None:
        _modify_tool.deactivate()