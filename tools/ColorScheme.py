# -*- coding: utf-8 -*-
"""ColorScheme —— 曲线 / 骨骼的 override 颜色方案

★ 配色原则：
    顶点颜色（权重可视化）会占用 黑 / 蓝 / 青 / 绿 / 黄 / 橙 / 红 / 白。
    本方案统一使用天蓝（Maya overrideColor 索引 18），与顶点颜色不冲突。

对象 → 颜色（当前均为天蓝）：
    脊线 loc 骨骼   天蓝
    脊线曲线        天蓝
    fwd 骨骼        天蓝
    fwd 曲线        天蓝
    bwd 骨骼        天蓝
    bwd 曲线        天蓝

★ 六个常量分开声明而非共用一个，是为了保留「将来按类别差异化配色」的扩展点：
  改其中任何一个，对应类别就独立变色。当前它们的值都等于 DEFAULT_COLOR。
"""
import maya.cmds as cmds


# ============================================================================
# 颜色索引（Maya overrideColor 索引 0~31）
# ============================================================================

DEFAULT_COLOR = 18   # 天蓝：所有脊线 / fwd / bwd 的曲线与骨骼统一使用

LOC_COLOR        = DEFAULT_COLOR   # 脊线 loc 骨骼
MAIN_CURVE_COLOR = DEFAULT_COLOR   # 脊线曲线
FWD_JOINT_COLOR  = DEFAULT_COLOR   # fwd 骨骼
FWD_CURVE_COLOR  = DEFAULT_COLOR   # fwd 曲线
BWD_JOINT_COLOR  = DEFAULT_COLOR   # bwd 骨骼
BWD_CURVE_COLOR  = DEFAULT_COLOR   # bwd 曲线


# ============================================================================
# 通用函数
# ============================================================================

def set_color(node, color_index):
    """给节点设置 override 颜色。

    若节点已处于目标状态（overrideEnabled=1 且 overrideColor=color_index），
    提前返回，避免无意义的 setAttr 触发 DG dirty。
    """
    if not node or not cmds.objExists(node):
        return
    color_index = int(color_index)
    try:
        enabled = cmds.getAttr(f"{node}.overrideEnabled")
        if enabled == 1:
            try:
                if int(cmds.getAttr(f"{node}.overrideColor")) == color_index:
                    return
            except Exception:
                pass
        cmds.setAttr(f"{node}.overrideEnabled", 1)
        cmds.setAttr(f"{node}.overrideColor", color_index)
    except Exception:
        pass


def clear_color(node):
    """清除节点 override 颜色（已为 0 则不动）。"""
    if not node or not cmds.objExists(node):
        return
    try:
        if cmds.getAttr(f"{node}.overrideEnabled") == 0:
            return
        cmds.setAttr(f"{node}.overrideEnabled", 0)
    except Exception:
        pass


# ============================================================================
# 便捷函数（语义化调用）
# ============================================================================

def colorize_loc(jnt):
    """脊线 loc 骨骼 → 天蓝"""
    set_color(jnt, LOC_COLOR)


def colorize_main_curve(crv_name):
    """脊线曲线 → 天蓝"""
    set_color(crv_name, MAIN_CURVE_COLOR)


def colorize_fwd_joint(jnt):
    """fwd 骨骼 → 天蓝"""
    set_color(jnt, FWD_JOINT_COLOR)


def colorize_fwd_curve(crv_name):
    """fwd 曲线 → 天蓝"""
    set_color(crv_name, FWD_CURVE_COLOR)


def colorize_bwd_joint(jnt):
    """bwd 骨骼 → 天蓝"""
    set_color(jnt, BWD_JOINT_COLOR)


def colorize_bwd_curve(crv_name):
    """bwd 曲线 → 天蓝"""
    set_color(crv_name, BWD_CURVE_COLOR)