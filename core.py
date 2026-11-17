# -*- coding: utf-8 -*-
"""weightCurveTool 主流程"""
import maya.cmds as cmds

from .Logger import log

from .tools.GroupManager import GroupManager
from .tools.VertexColorDisplay import VertexColorDisplay
from .tools.WeightCalculator import WeightCalculator
from .tools.SurfaceSlideBuilder import SurfaceSlideBuilder
from .tools.MayaSelectors import get_selected_mesh_transform, get_selected_joint
from .tools.JointState import apply_liw_on_pick, get_parent_joint
from .tools.SkinWeightPainter import is_in_skin

from .tools.WeightColorPipeline import (
    update_combined_vertex_colors as _wc_update,
    delete_color_set_and_clean_history as _wc_remove,
    init_weights_for_picked as _wc_init,
)

from .tools.ModifyCurveTool import (
    start_modify_curve_mode as _mct_start,
    stop_modify_curve_mode as _mct_stop,
)
from .tools.BoundaryFlow import (
    start_boundary_pick_mode as _bf_start,
    stop_boundary_pick_mode as _bf_stop,
)
from .tools.CreateCurveFlow import (
    start_continuous_surface_locator as _ccf_start,
    deactivate_surface_locator as _ccf_deactivate,
    rebuild_curves_from_set as _ccf_rebuild,
)


# ============================================================================
# 共享上下文
# ============================================================================

class _Context(object):
    """流程文件共享的状态容器（非全局单例，显式传递）。"""

    _DEFAULT_XS = (0.0, 0.5, 0.5, 1.0)
    _DEFAULT_YS = (0.0, 0.0, 1.0, 1.0)

    def __init__(self):
        self.group_mgr = GroupManager()
        self.weight_calc = WeightCalculator()
        self.color_display = VertexColorDisplay()
        self.slide_builder = SurfaceSlideBuilder()
        self.last_xs = self._DEFAULT_XS
        self.last_ys = self._DEFAULT_YS
        self.active_closed = False
        self.verbose_diagnostics = False

        self.surface_tool = None
        self.curve_builder = None
        self.continuous_mesh = None

        self.rest_surface = None
        self.reference_joint = None
        self.parent_joint = None

    def reset(self):
        if self.surface_tool is not None:
            try:
                self.surface_tool.deactivate()
            except Exception:
                pass
            self.surface_tool = None
        try:
            _mct_stop()
        except Exception:
            pass
        try:
            _bf_stop()
        except Exception:
            pass

        self.group_mgr = GroupManager()
        self.weight_calc = WeightCalculator()
        self.color_display = VertexColorDisplay()
        self.slide_builder = SurfaceSlideBuilder()

        self.last_xs = self._DEFAULT_XS
        self.last_ys = self._DEFAULT_YS
        self.active_closed = False
        self.curve_builder = None
        self.continuous_mesh = None
        self.rest_surface = None
        self.reference_joint = None
        self.parent_joint = None


_ctx = _Context()


# ============================================================================
# 权重模式
# ============================================================================

def set_weight_mode(mode):
    return _ctx.weight_calc.set_mode(mode)


def get_weight_mode():
    return _ctx.weight_calc.get_mode()


# ============================================================================
# 曲线参数
# ============================================================================

def set_curve_points(xs, ys):
    _ctx.last_xs = tuple(float(v) for v in xs)
    _ctx.last_ys = tuple(float(v) for v in ys)


def get_curve_points():
    return _ctx.last_xs, _ctx.last_ys


def ensure_rest_surface(mesh_name):
    """确保 _ctx.rest_surface 对应当前 mesh。换 mesh 时重建。"""
    if not mesh_name or not cmds.objExists(mesh_name):
        return None
    if _ctx.rest_surface is None or _ctx.rest_surface.mesh_name != mesh_name:
        from .tools.RestPoseSurface import RestPoseSurface
        _ctx.rest_surface = RestPoseSurface(mesh_name)
    return _ctx.rest_surface


def get_rest_surface():
    return _ctx.rest_surface


def refresh_curve_display(mesh_name):
    if _ctx.rest_surface is None:
        return 0
    from .tools import CurveLocalPoints
    return CurveLocalPoints.refresh_display_all(
        _ctx.rest_surface, prefix_filter=mesh_name)


def refresh_display_curves():
    from .tools import CurveLocalPoints
    if _ctx.rest_surface is None:
        return 0
    return CurveLocalPoints.refresh_display_all(_ctx.rest_surface)


def refresh_display_locs():
    from .tools.SurfaceBinder import rebind_all_joints
    if _ctx.rest_surface is None:
        return 0
    return rebind_all_joints(verbose=False, rest_surface=_ctx.rest_surface)


# ============================================================================
# UI「拾取骨骼」按钮入口
# ============================================================================

def pick_reference_joint(mesh_name=None):
    """读取 UI 指定/当前拾取的子骨骼，并筛选其父级蒙皮骨骼。

    ★ 本函数不自动设置 liw。liw 状态由用户手动控制。
    ★ 曲线边界 ik 模式的 liw 由 on_toggle_mode / on_redraw_weight_colors
      显式调用 apply_liw_for_curve_ik 设置。

    返回 (jnt, reason)：
        jnt     拾取到的骨骼名（成功）或 None（失败）
        reason  失败原因（成功时为 None）
    """
    if not mesh_name or not mesh_name.strip():
        reason = "请先在 UI 里选择模型"
        cmds.warning(reason)
        return None, reason
    if not cmds.objExists(mesh_name):
        reason = f"模型 '{mesh_name}' 不存在"
        cmds.warning(reason)
        return None, reason

    jnt = get_selected_joint()
    if not jnt:
        reason = "未选中 Joint"
        cmds.warning(reason)
        return None, reason

    if not is_in_skin(mesh_name, jnt):
        reason = f"子骨骼 '{jnt}' 不在 '{mesh_name}' 的蒙皮内"
        cmds.warning(reason)
        return None, reason

    # 父级 influence 可能不存在（独立骨骼 / 父级不是 skin influence）
    par_jnt = get_parent_joint(jnt, mesh_name=mesh_name)
    if par_jnt and not is_in_skin(mesh_name, par_jnt):
        par_jnt = None

    _ctx.reference_joint = jnt
    _ctx.parent_joint = par_jnt

    log.info("拾取骨骼: {}".format(jnt))
    log.info("父级蒙皮骨骼: {}".format(par_jnt if par_jnt else "（无）"))
    log.info("次级 source 与脊椎 liw 由用户配置；次级 pick/关节父子仅写入需要时临时解锁：")
    log.info("  骨骼投影-次级(soft)：source 手动 liw=0，pick 按需临时解锁")
    log.info("  骨骼投影-关节(ik)：pick 与 parent 按需临时解锁，自动识别链轴，其他锁不变")
    log.info("  骨骼投影-脊椎(spine)：所有未锁蒙皮 joint 必须组成一条单链，工具不改 liw")
    log.info("  曲线边界-ik：切到 ik 或 ik 模式重绘时会自动设置")
    return jnt, None


def get_reference_joint():
    return _ctx.reference_joint, _ctx.parent_joint

def _find_mesh_for_joint(jnt):
    """从 jnt 反查它所在 skinCluster 的 mesh transform。

    不依赖 _ctx.continuous_mesh，避免用户切换 UI 文本框后状态过期。
    """
    if not jnt or not cmds.objExists(jnt):
        return None
    for sk in (cmds.ls(type='skinCluster') or []):
        try:
            infs = cmds.skinCluster(sk, q=True, influence=True) or []
        except Exception:
            continue
        if jnt in infs:
            try:
                geos = cmds.skinCluster(sk, q=True, geometry=True) or []
            except Exception:
                continue
            if geos:
                pars = cmds.listRelatives(geos[0], parent=True) or []
                return pars[0] if pars else geos[0]
    return None

def apply_liw_for_curve_ik():
    """曲线边界（soft / ik 通用）：解锁 picked 和 par，其他锁定。

    由 on_toggle_mode（切换模式）和 on_redraw_weight_colors（重绘）调用。
    mesh 从 reference_joint 反查 skinCluster，不依赖 continuous_mesh。

    ★ 修复：
      1. 之前版本 apply_liw_on_pick 会把 picked 也锁定，导致 paint_edit_joints 拒绝写入
      2. 之前版本依赖 _ctx.continuous_mesh，用户没先创建曲线时会失败
    """
    picked = _ctx.reference_joint
    par = _ctx.parent_joint
    if not picked or not par:
        log.warn(
            "apply_liw_for_curve_ik：缺少 reference_joint 或 parent_joint\n"
            "  请先在 UI 拾取骨骼，并确保它有父级 skin influence")
        return False
    mesh = _ctx.continuous_mesh or _find_mesh_for_joint(picked)
    if not mesh:
        log.warn(
            f"apply_liw_for_curve_ik：无法确定 '{picked}' 所在 mesh\n"
            f"  请先通过「创建曲线」确认当前工作 mesh")
        return False
    try:
        apply_liw_on_pick(picked, mesh_name=mesh, parent_joint=par)
        log.info(
            f"曲线边界：已解锁 '{picked}'（子）和 '{par}'（父），其他锁定")
        return True
    except Exception as e:
        log.warn(f"apply_liw_on_pick 失败: {e}")
        return False


# ============================================================================
# 创建曲线
# ============================================================================

def start_continuous_surface_locator(mesh_name, direction_joint):
    _ccf_start(mesh_name, direction_joint, _ctx)


def deactivate_surface_locator():
    _ccf_deactivate(_ctx)


def rebuild_curves_from_set(set_name=None, kind='main', closed=False):
    return _ccf_rebuild(set_name, kind, closed, _ctx)


# ============================================================================
# 生成边界
# ============================================================================

def start_boundary_pick_mode(mesh_name):
    _bf_start(mesh_name, _ctx)


def stop_boundary_pick_mode():
    _bf_stop()


# ============================================================================
# 修改曲线
# ============================================================================

def start_modify_curve_mode():
    _mct_start(_ctx)


def stop_modify_curve_mode():
    _mct_stop()


# ============================================================================
# set 列表
# ============================================================================

def list_sets():
    sets = _ctx.group_mgr.list_sets()
    log.info(f"可用 set: {sets}")
    return sets


# ============================================================================
# 权重计算 + 颜色可视化
# ============================================================================

def init_weights_for_picked(mesh_name):
    return _wc_init(_ctx, mesh_name)


def update_combined_vertex_colors(mesh_name, xs=None, ys=None,
                                  closed=None, joints=None):
    return _wc_update(_ctx, mesh_name, xs=xs, ys=ys,
                      closed=closed, joints=joints)


def delete_color_set_and_clean_history(mesh_name,
                                       color_set_name="geoWeightVis"):
    return _wc_remove(_ctx, mesh_name, color_set_name=color_set_name)


# ============================================================================
# 骨骼投影（Legacy tab）
# ============================================================================

def paint_legacy(mode, mesh_name, xs=None, ys=None, r=1.0, target_joint=None, range_mode='auto',reference_space='current'):
    """骨骼投影 tab 入口。

    mode ∈ soft/ik/spine/brow/belt/cloth/eye
    target_joint：soft/ik 的写入目标（UI pick）；spine 由未锁单链确定。
                  range_mode 仅供 ik/spine：auto 或 selection。
    """
    from .tools.LegacyWeightPainter import paint as _paint
    return _paint(mode, mesh_name, xs=xs, ys=ys, r=r,
                  target_joint=target_joint, range_mode=range_mode,reference_space=reference_space)


def preview_legacy_range(mode,mesh_name,target_joint=None,range_mode='auto',r=1.,return_plan=False,reference_space='current'):
    from .tools.LegacyWeightPainter import preview_range,preview_assignment_plan
    if return_plan:return preview_assignment_plan(mode,mesh_name,target_joint,range_mode,r,reference_space)
    return preview_range(mode,mesh_name,target_joint=target_joint,range_mode=range_mode,r=r,reference_space=reference_space)


def find_skin_cluster(mesh_name):
    from .tools.LegacyWeightPainter import _find_skin_cluster
    return _find_skin_cluster(mesh_name)


# ============================================================================
# 重置
# ============================================================================

def reset_weight_history(mesh_name):
    try:
        _ccf_deactivate(_ctx)
    except Exception:
        pass
    try:
        _mct_stop()
    except Exception:
        pass
    try:
        _bf_stop()
    except Exception:
        pass

    _ctx.active_closed = False
    _ctx.group_mgr.clear_all()
    _ctx.color_display.remove(mesh_name)
    log.ok("已重置")


def reset_context():
    _ctx.reset()

def set_verbose_diagnostics(enabled=True):
    """Enable expensive identity diagnostics; ordinary errors remain visible."""
    _ctx.verbose_diagnostics = bool(enabled)
