# -*- coding: utf-8 -*-
"""DualCurveBuilder —— 根据模型 + 关节列表创建曲线

display_mode 控制"生成哪种曲线"：
  · "laplacian"（默认）→ 只生成 _ring_laplacian
  · "gaussian"          → 只生成 _ring_gaussian

不生成的那种如果场景里已存在，会被删除。
"""
import maya.cmds as cmds
import numpy as np
import json

from ..Logger import log
from . import CurveRefine as CR
from . import ColorScheme
from . import CurveLocalPoints
from .BoundaryCorrespondence import deform_ordered_points, resample_anchor_intervals


# ============================================================================
# 主类
# ============================================================================

class DualCurveBuilder(object):

    def __init__(self,
                 strength="strong",
                 display_mode="laplacian",
                 samples_per_segment=60,
                 internal_per_segment=2,
                 curve_line_width=5,
                 lap_color=ColorScheme.MAIN_CURVE_COLOR,
                 gauss_color=ColorScheme.MAIN_CURVE_COLOR,
                 draw_on_top=False,
                 offset_mode="adaptive",
                 offset_distance=0.02,
                 min_offset=0.005,
                 max_offset=0.08,
                 offset_margin=0.005,
                 rest_surface=None):
        self.strength = strength
        self.display_mode = display_mode
        self.samples_per_segment = int(samples_per_segment)
        self.internal_per_segment = int(internal_per_segment)
        self.curve_line_width = float(curve_line_width)
        self.lap_color = int(lap_color)
        self.gauss_color = int(gauss_color)
        self.draw_on_top = bool(draw_on_top)
        self.offset_mode = offset_mode
        self.offset_distance = float(offset_distance)
        self.min_offset = float(min_offset)
        self.max_offset = float(max_offset)
        self.offset_margin = float(offset_margin)
        self.rest_surface = rest_surface

        self._current = {
            'mesh': None,
            'joints': [],
            'closed': False,
        }

    # ==================== 主入口 ====================

    def create(self, mesh_name, joints, closed=False, verbose=True, name_prefix=None):
        # ★ 每次构建前清缓存：mesh 可能已变形，KDTree 必须重新取点
        CR.clear_mesh_fn_cache()

        joints = self._normalize_joints(joints)

        if not cmds.objExists(mesh_name):
            if verbose: log.warn(f"网格 '{mesh_name}' 不存在")
            return None
        if len(joints) < 2:
            if verbose: log.info("至少需要 2 个关节")
            return None
        for jnt in joints:
            if not cmds.objExists(jnt):
                log.warn(f"关节 '{jnt}' 不存在")
                return None
        shapes = cmds.listRelatives(mesh_name, shapes=True, type="mesh")
        if not shapes:
            log.warn(f"'{mesh_name}' 不是有效网格")
            return None
        shape_name = shapes[0]

        raw = self._read_joint_rest_positions(joints, mesh_name)
        if raw is None:
            if verbose:
                log.info("  loc 无 wct_* 或 rest_surface 未传，fallback 当前显示位置")
            raw = [np.array(cmds.xform(jnt, q=True, ws=True, t=True)) for jnt in joints]

        center = None
        if len(raw) >= 3:
            fit = CR.fit_circle_3d(raw)
            if fit is not None:
                center = fit[0]
        if center is None:
            center = np.mean(np.array(raw, dtype=float), axis=0)

        prefix = name_prefix if name_prefix else mesh_name
        name_lap = f"{prefix}_ring_laplacian"
        name_gau = f"{prefix}_ring_gaussian"

        if verbose:
            log.info(f"创建曲线 (joints={len(joints)}, closed={closed}, "
                     f"strength={self.strength}, mode={self.display_mode})")
            if name_prefix:
                log.info(f"  前缀: {prefix}")

        paired = self._boundary_points(joints, raw, closed)
        if paired is not None:
            use_gaussian = self.display_mode == "gaussian"
            chosen = name_gau if use_gaussian else name_lap
            other = name_lap if use_gaussian else name_gau
            color = self.gauss_color if use_gaussian else self.lap_color
            crv_name = self._create_ordered(shape_name, paired, chosen, closed,
                                             color, center, verbose, enforce_order=True)
            if cmds.objExists(other):
                cmds.delete(other)
            self._current.update(mesh=mesh_name, joints=list(joints), closed=closed)
            if verbose:
                log.info("[BoundaryPair V17.3.4] preserved {} source EPs".format(len(paired)))
            return crv_name

        # ★ 按 display_mode 只生成一种曲线；另一种如果存在就删掉
        crv_name = None
        if self.display_mode == "gaussian":
            gau_path, gau_anchors = self._build_path_gaussian(shape_name, raw, closed)
            if gau_path is not None:
                crv_name = self._create_single(shape_name, gau_path, gau_anchors,
                                               name_gau, closed, self.gauss_color,
                                               center, verbose)
            if cmds.objExists(name_lap):
                cmds.delete(name_lap)
        else:
            lap_path, lap_anchors = self._build_path_laplacian(shape_name, raw, closed)
            if lap_path is not None:
                crv_name = self._create_single(shape_name, lap_path, lap_anchors,
                                               name_lap, closed, self.lap_color,
                                               center, verbose)
            if cmds.objExists(name_gau):
                cmds.delete(name_gau)

        self._current['mesh'] = mesh_name
        self._current['joints'] = list(joints)
        self._current['closed'] = closed

        return crv_name

    def remove(self, mesh_name=None):
        target = mesh_name or self._current.get('mesh')
        if target:
            for suffix in ["_ring_laplacian", "_ring_gaussian"]:
                name = f"{target}{suffix}"
                if cmds.objExists(name):
                    cmds.delete(name)
        self._current.update({'mesh': None, 'joints': [], 'closed': False})
        log.ok("已删除曲线")

    # ==================== 参数调整 ====================

    def set_strength(self, s):
        if s not in CR.STRENGTH_PRESETS:
            log.warn(f"无效强度，可选: {list(CR.STRENGTH_PRESETS.keys())}")
            return
        self.strength = s
        log.info(f"强度: {s}")
        self._rebuild_if_ready()

    def set_display_mode(self, mode):
        """切换生成 laplacian 还是 gaussian；切换后自动重建"""
        if mode not in ("laplacian", "gaussian"):
            log.warn("无效模式，可选: laplacian / gaussian")
            return
        self.display_mode = mode
        log.info(f"曲线类型切换为: {mode}")
        self._rebuild_if_ready()

    def toggle_display(self):
        seq = ["laplacian", "gaussian"]
        i = seq.index(self.display_mode) if self.display_mode in seq else 0
        self.set_display_mode(seq[(i + 1) % len(seq)])

    def set_offset_mode(self, mode, distance=None):
        if mode not in ("radial", "adaptive", "normal", "none"):
            log.warn("无效模式")
            return
        self.offset_mode = mode
        if distance is not None:
            self.offset_distance = float(distance)
        log.info(f"抬升模式: {self.offset_mode}")
        self._rebuild_if_ready()

    def set_adaptive_params(self, min_off=None, max_off=None, margin=None):
        if min_off is not None: self.min_offset = float(min_off)
        if max_off is not None: self.max_offset = float(max_off)
        if margin is not None: self.offset_margin = float(margin)
        log.info(f"自适应: min={self.min_offset}, max={self.max_offset}, "
                 f"margin={self.offset_margin}")
        if self.offset_mode == "adaptive":
            self._rebuild_if_ready()

    def set_draw_on_top(self, enabled=True):
        self.draw_on_top = bool(enabled)
        mesh_name = self._current.get('mesh')
        if mesh_name:
            for suffix in ["_ring_laplacian", "_ring_gaussian"]:
                name = f"{mesh_name}{suffix}"
                if cmds.objExists(name):
                    try:
                        cmds.setAttr(f"{name}.alwaysDrawOnTop",
                                     1 if self.draw_on_top else 0)
                    except Exception:
                        pass
        log.info(f"alwaysDrawOnTop = {self.draw_on_top}")

    # ==================== 内部 ====================

    def _normalize_joints(self, joints):
        """把 joints 输入规范化为列表。

        ★ joints is None → 返回 []，避免 str(None) = "None" 污染下游 objExists。
        """
        if joints is None:
            return []
        if isinstance(joints, str):
            return [joints]
        try:
            return [str(jnt) for jnt in joints]
        except TypeError:
            return [str(joints)]
            
            
    def _read_joint_rest_positions(self, joints, mesh_name):
        """从 wct_* 反推 loc 的 rest pose 位置。
    
        全部 joint 都有 wct_* 且 mesh 匹配时返回 list of np.ndarray；
        否则返回 None（调用方 fallback 当前显示位置）。
        """
        if self.rest_surface is None:
            return None
        positions = []
        for jnt in joints:
            pos = self.rest_surface.get_joint_rest_position(jnt)
            if pos is None:
                return None
            # 确认 wct_mesh 匹配（get_joint_rest_position 已检查，这里只做防御）
            try:
                wct_mesh = cmds.getAttr(f"{jnt}.wct_mesh")
            except Exception:
                return None
            if wct_mesh != mesh_name:
                return None
            positions.append(np.asarray(pos, dtype=float))
        return positions            
            

    def _build_path_laplacian(self, shape_name, points, closed):
        return CR.build_dense_laplacian(
            shape_name, points, closed,
            samples_per_segment=self.samples_per_segment,
            strength=self.strength,
            rest_surface=self.rest_surface)
    
    def _build_path_gaussian(self, shape_name, points, closed):
        return CR.build_dense_gaussian(
            shape_name, points, closed,
            samples_per_segment=self.samples_per_segment,
            strength=self.strength,
            rest_surface=self.rest_surface)

    def _apply_offset(self, shape_name, points, center):
        if self.offset_mode == "none":
            return [np.asarray(p, dtype=float) for p in points]
        if center is None:
            center = np.mean(np.array(points, dtype=float), axis=0)
        if self.offset_mode == "radial":
            return CR.offset_radial(points, center, self.offset_distance)
        if self.offset_mode == "adaptive":
            return CR.offset_adaptive(shape_name, points, center,
                                      self.min_offset, self.max_offset,
                                      self.offset_margin)
        if self.offset_mode == "normal":
            return CR.offset_normal(shape_name, points, self.offset_distance)
        return [np.asarray(p, dtype=float) for p in points]

    def _create_single(self, shape_name, dense, anchors, name,
                       closed, color, center, verbose):
        simplified = CR.simplify_by_anchors(dense, anchors, self.internal_per_segment)
        if len(simplified) < 2:
            return None
        if closed and len(simplified) >= 4:
            first = np.asarray(simplified[0], dtype=float)
            last = np.asarray(simplified[-1], dtype=float)
            if np.linalg.norm(first - last) < 1e-6:
                simplified = simplified[:-1]
    
        return self._create_ordered(shape_name, simplified, name, closed,
                                    color, center, verbose)

    def _create_ordered(self, shape_name, simplified, name, closed, color, center, verbose, enforce_order=False):
        """Create exactly the supplied EPs without another simplification."""
        # ★ 记录 rest pose EP（offset 之前，权重计算用）
        local_points = np.array(
            [np.asarray(p, dtype=float) for p in simplified], dtype=float)
    
        # offset 后的世界位置（视觉用）
        simplified = [p.tolist() for p in self._apply_offset(shape_name, simplified, center)]
    
        if cmds.objExists(name):
            cmds.delete(name)
    
        if closed and len(simplified) >= 4:
            crv = CR.create_periodic_curve(simplified, name)
            if crv is None:
                degree = 3 if len(simplified) >= 4 else 1
                crv = cmds.curve(d=degree, ep=simplified, name=name)
            tag = "periodic"
        else:
            degree = 3 if len(simplified) >= 4 else 1
            crv = cmds.curve(d=degree, ep=simplified, name=name)
            tag = "open"
    
        # Periodic construction must not introduce a one-knot phase shift.
        # Use the same component assignment as the existing display refresh.
        if enforce_order and not self._assign_ordered_eps(crv, simplified):
            raise RuntimeError("Curve EP count differs from source correspondence")

        # ★ 存 wcs_localPoints（offset 前 rest pose）+ wcs_displayPoints（offset 后世界位置）
        try:
            CurveLocalPoints.write(crv, local_points, np.array(simplified, dtype=float))
        except Exception as e:
            if verbose:
                log.warn(f"  写曲线坐标属性失败: {e}")
    
        ColorScheme.set_color(crv, color)
        try:
            cmds.setAttr(f"{crv}.lineWidth", self.curve_line_width)
        except Exception:
            pass
        try:
            cmds.setAttr(f"{crv}.alwaysDrawOnTop", 1 if self.draw_on_top else 0)
        except Exception:
            pass
    
        if verbose:
            form = cmds.getAttr(f"{crv}.form")
            fs = {0: "open", 1: "closed", 2: "periodic"}.get(form, "?")
            log.info(f"  {name}: {len(simplified)} EP, form={fs} ({tag})")
        return crv

    def _assign_ordered_eps(self, curve, points):
        components=cmds.ls(curve+".ep[*]",fl=True) or []
        if len(components)==len(points)+1:
            # Some closed representations expose a duplicate seam endpoint.
            first=np.asarray(cmds.xform(components[0],q=True,ws=True,t=True))
            last=np.asarray(cmds.xform(components[-1],q=True,ws=True,t=True))
            if np.linalg.norm(first-last)<1e-7:
                components=components[:-1]
        if len(components)!=len(points):
            return False
        for ep,p in zip(components,points):
            cmds.xform(ep,ws=True,t=list(map(float,p)))
        actual=np.asarray([cmds.xform(ep,q=True,ws=True,t=True) for ep in components])
        if not np.allclose(actual,np.asarray(points),rtol=0,atol=1e-5):
            raise RuntimeError("Boundary EP assignment changed neighbouring points")
        return True

    def set_boundary_correspondence(self, mesh_name, joints, display_points,
                                    anchor_indices, closed):
        """Persist the full sliding result on a side control, surviving curve rebuilds."""
        if not joints or len(joints)!=len(anchor_indices):
            raise RuntimeError("Missing boundary EP/control correspondence")
        local=np.asarray(display_points,dtype=float)
        if self.rest_surface is not None:
            local=np.asarray(self.rest_surface.display_to_local(local),dtype=float)
        raw=self._read_joint_rest_positions(joints,mesh_name)
        if raw is None:
            raw=[cmds.xform(j,q=True,ws=True,t=True) for j in joints]
        # Use actual bound control positions, not a second nearest-curve search.
        local[np.asarray(anchor_indices,dtype=int)]=np.asarray(raw,dtype=float)
        deform_ordered_points(local,anchor_indices,raw,raw,closed)  # validate
        state=dict(version=1,closed=bool(closed),mesh=mesh_name,
                   uuids=[cmds.ls(j,uuid=True)[0] for j in joints],
                   points=local.tolist(),anchor_indices=list(map(int,anchor_indices)),
                   anchor_positions=np.asarray(raw,dtype=float).tolist())
        attr="wct_boundaryCorrespondence"
        if not cmds.attributeQuery(attr,node=joints[0],exists=True):
            cmds.addAttr(joints[0],ln=attr,dt="string")
        cmds.setAttr(joints[0]+"."+attr,json.dumps(state,allow_nan=False),type="string")

    def _boundary_points(self, joints, raw, closed):
        attr="wct_boundaryCorrespondence"
        states=[j for j in joints if cmds.attributeQuery(attr,node=j,exists=True)]
        if not states:
            return None  # Legacy curves/main curves retain the existing builder.
        state=json.loads(cmds.getAttr(states[0]+"."+attr))
        if (state.get('version')!=1 or bool(state['closed'])!=bool(closed)
                or state['uuids']!=[cmds.ls(j,uuid=True)[0] for j in joints]):
            raise RuntimeError("Boundary control order changed; regenerate boundaries from ridge")
        # Source indices stay fixed, but independently slid intermediate EPs
        # are NOT used as a shape guide: on a curved surface those trajectories
        # can bunch up or cross. Refit only between the ordered control anchors.
        ids=np.asarray(state['anchor_indices'],dtype=int)
        order=np.argsort(ids)
        shape=cmds.listRelatives(state['mesh'],shapes=True,type='mesh')[0]
        build=(self._build_path_gaussian if self.display_mode=="gaussian"
               else self._build_path_laplacian)
        dense,anchors=build(shape,np.asarray(raw,dtype=float)[order],closed)
        if dense is None or anchors is None:
            raise RuntimeError("Cannot build anchored boundary surface path")
        points=resample_anchor_intervals(dense,anchors,ids[order],len(state['points']),closed)
        if self.rest_surface is not None:
            points=np.asarray(self.rest_surface.project_to_surface(points),dtype=float)
        points[ids]=np.asarray(raw,dtype=float)
        return points

    def _rebuild_if_ready(self):
        if self._current['mesh'] and self._current['joints']:
            self.create(self._current['mesh'], self._current['joints'],
                        self._current['closed'], verbose=True)