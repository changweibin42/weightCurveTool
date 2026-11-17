# -*- coding: utf-8 -*-
"""V17.8.15 ModifyCurveTool —— 可见 loc 拾取与连续贴面拖动

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
import heapq
import math
from time import perf_counter

from ..Logger import log
from .DualCurveBuilder import DualCurveBuilder
from .CurveBuildReuse import create_if_changed
from .JointState import lock_joint, unlock_joint, lock_all_locs
from .SurfaceBinder import (
    _ensure_bind_attrs,
    rebind_all_joints,
    infer_mesh_from_joint,
)
from .WeightColorPipeline import update_combined_vertex_colors
from .MayaSelectors import find_main_locs, _loc_sort_key
from .NativeCurvePreview import NativeCurvePreview, FrozenMapping


def _sphere_entry(origin,direction,center,radius):
    """First forward sphere intersection; direction must be unit length."""
    delta=np.asarray(origin)-np.asarray(center)
    along=float(np.dot(delta,direction))
    disc=along*along-float(np.dot(delta,delta))+radius*radius
    if disc<0:return None
    root=math.sqrt(max(0.,disc));near=-along-root;far=-along+root
    return max(0.,near) if far>=0 else None


def _surface_graph(points,polygons):
    graph=[{} for _ in points];lengths=[]
    for face in polygons:
        for a,b in zip(face,face[1:]+face[:1]):
            if b in graph[a]:continue
            length=float(np.linalg.norm(points[b]-points[a]))
            if length<=1e-12:continue
            graph[a][b]=graph[b][a]=length;lengths.append(length)
    return graph,float(np.median(lengths)) if lengths else 0.


def _surface_step_allowed(previous,current,points,polygons,graph,median):
    """A bounded surface path rejects jumps between close, remote sheets."""
    f0,f1=previous['face'],current['face']
    if f0==f1:return True
    p0,p1=previous['point'],current['point']
    source,target=polygons[f0],polygons[f1]
    local=[]
    for face in (source,target):
        local.extend(graph[a][b] for a,b in zip(face,face[1:]+face[:1]) if b in graph[a])
    scale=float(np.median(local)) if local else median
    budget=max(4.*scale,3.*float(np.linalg.norm(p1-p0)),1e-8)
    targets={v:float(np.linalg.norm(points[v]-p1)) for v in target}
    costs={v:float(np.linalg.norm(points[v]-p0)) for v in source}
    queue=[(cost,v) for v,cost in costs.items() if cost<=budget];heapq.heapify(queue)
    visited=0
    while queue and visited<1024:
        cost,v=heapq.heappop(queue)
        if cost!=costs[v]:continue
        visited+=1
        if v in targets and cost+targets[v]<=budget:return True
        for nb,length in graph[v].items():
            new=cost+length
            if new<=budget and new<costs.get(nb,float('inf')):
                costs[nb]=new;heapq.heappush(queue,(new,nb))
    return False


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
    values=dict(wct_mesh=mesh_name,wct_id0=int(ids[0]),wct_id1=int(ids[1]),
                wct_id2=int(ids[2]),wct_u=float(u),wct_v=float(v),wct_w=float(w))
    previous={}
    def write(name,value):
        if name=='wct_mesh':cmds.setAttr(jnt+'.'+name,value,type='string')
        else:cmds.setAttr(jnt+'.'+name,value)
    try:
        previous={name:cmds.getAttr(jnt+'.'+name) for name in values}
        for name,value in values.items():write(name,value)
        return True
    except Exception as e:
        for name,value in previous.items():
            try:write(name,value)
            except Exception:pass
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
        self._clear_drag_surface()

    def _clear_drag_surface(self):
        self._surface_polygons=None
        self._surface_graph=None
        self._surface_median=0.
        self._last_surface_hit=None
        self._last_drag_pixel=None
        self._cursor_offset=None
        self._picked_surface_hit=None
        self._picked_center_pixel=None
        self._drag_block_notice=False

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
        log.info("修改曲线 V17.8.15：拖动当前会话可见 loc；遮挡不拾取，离面/跨层停在最后有效位置")
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
        self._clear_drag_surface()

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
                create_if_changed(builder, mesh_name, jnts, closed, prefix)
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

    @staticmethod
    def _screen_ray(vpX,vpY):
        view=omui.M3dView.active3dView()
        pos=om.MPoint();direction=om.MVector()
        view.viewToWorld(int(round(vpX)),int(round(vpY)),pos,direction)
        origin=np.asarray([pos.x,pos.y,pos.z],dtype=float)
        ray=np.asarray([direction.x,direction.y,direction.z],dtype=float)
        length=float(np.linalg.norm(ray))
        if length<=1e-12:return None
        return origin,ray/length

    def _surface_hit_at_vp(self,vpX,vpY):
        if self._deformed_fn is None:return None
        ray=self._screen_ray(vpX,vpY)
        if ray is None:return None
        return self._surface_hit_ray(*ray)

    def _surface_hit_ray(self,origin,direction):
        hit=self._deformed_fn.closestIntersection(
            om.MFloatPoint(*origin),om.MFloatVector(*direction),
            om.MSpace.kWorld,1e10,False)
        if not hit:return None
        ids=np.asarray(self._deformed_fn.getPolygonTriangleVertices(int(hit[2]),int(hit[3])),dtype=int)
        weights=np.asarray([float(hit[4]),float(hit[5]),1.-float(hit[4])-float(hit[5])])
        if len(ids)!=3 or np.min(ids)<0 or np.max(ids)>=len(self._deformed_pts):return None
        if not np.all(np.isfinite(weights)) or np.min(weights)<-1e-5:return None
        weights=np.clip(weights,0.,1.);weights/=weights.sum()
        point=(self._deformed_pts[ids]*weights[:,None]).sum(axis=0)
        return dict(point=point,face=int(hit[2]),triangle=int(hit[3]),
                    ids=ids.tolist(),weights=weights.tolist(),depth=float(hit[1]))

    def _visibility_ray(self,center,x,y):
        # Integer viewport projection can move a ray half a pixel away from
        # the locator. Test the exact center ray so sloped front surfaces are
        # not incorrectly classified as occluded.
        origin,direction=self._screen_ray(x,y)
        camera=omui.M3dView.active3dView().getCamera()
        if om.MFnCamera(camera).isOrtho:
            depth=float(np.dot(center-origin,direction))
            return center-direction*depth,direction
        matrix=camera.inclusiveMatrix()
        eye=np.asarray([matrix.getElement(3,i) for i in range(3)])
        direction=center-eye
        length=float(np.linalg.norm(direction))
        if length<=1e-12:return None
        direction/=length
        near=max(0.,float(np.dot(origin-eye,direction)))
        return eye+direction*near,direction

    def _ray_hit_mesh_at_vp(self,vpX,vpY):
        # Compatibility helper. Dragging uses the full hit record below.
        hit=self._surface_hit_at_vp(vpX,vpY)
        return None if hit is None else hit['point']

    def _drag_surface_target(self,vpX,vpY):
        target=np.asarray([vpX,vpY],dtype=float)+self._cursor_offset
        start=self._last_drag_pixel
        if float(np.linalg.norm(target-start))<1e-9:return None
        steps=max(1,int(math.ceil(float(np.linalg.norm(target-start))/8.)))
        if steps>64:return None
        current=self._last_surface_hit;accepted=None
        for i in range(1,steps+1):
            pixel=start+(target-start)*(i/float(steps))
            hit=self._surface_hit_at_vp(*pixel)
            if hit is None or not _surface_step_allowed(current,hit,self._deformed_pts,
                    self._surface_polygons,self._surface_graph,self._surface_median):
                if not self._drag_block_notice:
                    log.info('拖动暂停：无有效表面命中或检测到跨层跳跃；保留最后有效位置')
                    self._drag_block_notice=True
                break
            current=hit;accepted=(hit,pixel)
        return accepted

    # ==================== 拖拽事件 ====================

    def _on_press(self, *args):
        if not self._active:
            return
        try:
            vpX, vpY, _ = cmds.draggerContext(self.CTX, q=True, anchorPoint=True)
        except Exception as e:
            log.warn(f"取按下位置失败: {e}")
            return

        # Do not repick an already-held locator. Only current-session locs
        # are candidates, and all visibility tests use the same mesh snapshot.
        if self._picked:return
        self._clear_drag_surface()
        mesh_name=(self.ctx.group_mgr.find_mesh_from_active() or self.ctx.continuous_mesh)
        self._force_mesh_dirty(mesh_name)
        self._deformed_fn=self._get_deformed_mesh_fn(mesh_name)
        self._deformed_pts=self._get_mesh_world_points(self._deformed_fn)
        if self._deformed_fn is None or self._deformed_pts is None:return
        self._mesh_name=mesh_name
        try:
            self._surface_polygons=[list(map(int,self._deformed_fn.getPolygonVertices(f)))
                                    for f in range(int(self._deformed_fn.numPolygons))]
            self._surface_graph,self._surface_median=_surface_graph(
                self._deformed_pts,self._surface_polygons)
            jnt=self._pick_jnt_ray(int(vpX),int(vpY))
        except Exception as exc:
            log.warn('准备贴面拖动失败: {}'.format(exc));return
        if jnt is None:return
        self._last_surface_hit=self._picked_surface_hit
        self._last_drag_pixel=np.asarray(self._picked_center_pixel,dtype=float)
        self._cursor_offset=self._last_drag_pixel-np.asarray([vpX,vpY],dtype=float)

        p0 = np.array(cmds.xform(jnt, q=True, ws=True, t=True))
        self._p0_world = p0
        self._plane_center = None
        self._drag_offset_cam = None

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
        if not self._picked or not cmds.objExists(self._jnt):return
        try:
            vpX,vpY,_=cmds.draggerContext(self.CTX,q=True,dragPoint=True)
            accepted=self._drag_surface_target(vpX,vpY)
        except Exception as exc:
            log.warn('贴面拖动失败，保留原位置: {}'.format(exc));return
        if accepted is None:return
        hit,pixel=accepted
        ids=hit['ids'];u,v,w=hit['weights'];new_pos=hit['point']
        rs=self.ctx.rest_surface
        if rs is not None and len(rs.core.vertices)!=len(self._deformed_pts):
            log.warn('模型与绑定参考顶点数不一致，停止本次拖动');return
        # Keep the exact ray-hit triangle and barycentrics. A global nearest
        # surface query here could remap a thin/folded surface to its other side.
        if not self._undo_open:
            cmds.undoInfo(openChunk=True)
            unlock_joint(self._jnt)
            self._undo_open=True
        _ensure_bind_attrs(self._jnt)
        if not _write_bind_attrs(self._jnt,self._mesh_name,ids,u,v,w):return
        _set_pos_lock(self._jnt,lock=False)
        try:
            cmds.xform(self._jnt,ws=True,t=tuple(map(float,new_pos)))
        finally:
            _set_pos_lock(self._jnt,lock=True)
        self._last_surface_hit=hit
        self._last_drag_pixel=pixel
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
        self._clear_drag_surface()

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

        started = stage = perf_counter()
        rebuilt_count = reused_count = 0
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

        rebound_time = perf_counter() - stage
        stage = perf_counter()
        # ---- 步骤 2：仅跳过输入与保存坐标均未变化的曲线 ----
        for kind_iter in ('main', 'fwd', 'bwd'):
            jnts_iter = self.ctx.group_mgr.get_joints_from_active(kind_iter)
            if len(jnts_iter) < 2:
                continue
            jnts_iter = sorted(jnts_iter, key=_loc_sort_key)
            prefix = self._curve_prefix(mesh_name, kind_iter)
            try:
                curve, reused = create_if_changed(
                    builder, mesh_name, jnts_iter, closed, prefix)
                reused_count += int(reused)
                rebuilt_count += int(not reused)
                self.ctx.group_mgr.move_curves_to_active(mesh_name,
                                                         kind=kind_iter)
            except Exception as e:
                log.warn(f"重建 {kind_iter} 曲线失败: {e}")

        build_time = perf_counter() - stage
        stage = perf_counter()
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

        display_time = perf_counter() - stage
        stage = perf_counter()
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

        log.info("[WCT 松手耗时] 重贴={:.3f}s 曲线={:.3f}s "
                 "显示={:.3f}s 权重流程={:.3f}s 总计={:.3f}s "
                 "重建={} 复用={}".format(
                     rebound_time, build_time, display_time,
                     perf_counter()-stage, perf_counter()-started,
                     rebuilt_count, reused_count))
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
            locs=[]
            for kind in ('main','fwd','bwd'):
                locs.extend(self.ctx.group_mgr.get_joints_from_active(kind) or [])
            locs=list(dict.fromkeys(locs))
            # Full paths also remove ambiguity from duplicate joint names.
            if not locs:return None
            locs=cmds.ls(locs,long=True,visible=True,type='joint') or []
            ray=self._screen_ray(vpX,vpY)
            if not locs or ray is None:return None
            origin,direction=ray;view=omui.M3dView.active3dView()
            scale=_get_joint_display_scale()*.5
            magnitude=max(1.,float(np.max(np.abs(self._deformed_pts))))
            tolerance=max(1e-6,magnitude*2e-7)
            candidates=[]
            for jnt in locs:
                try:
                    center=np.asarray(cmds.xform(jnt,q=True,ws=True,t=True),dtype=float)
                    radius=float(cmds.getAttr(jnt+'.radius'))
                    radius=(radius if radius>1e-6 else 1.)*scale
                    entry=_sphere_entry(origin,direction,center,radius)
                    if entry is None:continue
                    x,y,visible=view.worldToView(om.MPoint(*center))
                    if not visible:continue
                    center_ray=self._visibility_ray(center,x,y)
                    if center_ray is None:continue
                    surface=self._surface_hit_ray(*center_ray)
                    if surface is None:continue
                    co,cd=center_ray
                    depth=float(np.dot(center-co,cd))
                    if depth<=0 or surface['depth']<depth-tolerance:continue
                    candidates.append((entry,jnt,surface,(x,y)))
                except Exception:
                    continue
            if not candidates:return None
            _,joint,surface,pixel=min(candidates,key=lambda row:(row[0],row[1]))
            self._picked_surface_hit=surface;self._picked_center_pixel=pixel
            return joint
        except Exception as exc:
            log.warn('拾取失败: {}'.format(exc));return None


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
