# -*- coding: utf-8 -*-
"""权重可视化：把 float 权重数组映射为顶点颜色"""
import maya.cmds as cmds
import maya.api.OpenMaya as om

from ..Logger import log


# 颜色分档表：w 字段严格递增（恒有 w1 > w0），内部插值不除零
WEIGHT_COLOR_STOPS = [
    (0.0, (0.0, 0.0, 1.0)),
    (0.4, (0.0, 1.0, 0.0)),
    (0.6, (1.0, 1.0, 0.0)),
    (0.8, (1.0, 0.5, 0.0)),
    (1.0, (1.0, 0.0, 0.0)),
]

# 清理历史节点时，按"下游 → 上游"顺序处理
_HISTORY_ORDER = ('polyColorPerVertex', 'createColorSet', 'deleteColorSet')


class VertexColorDisplay(object):
    def __init__(self, color_set_name="geoWeightVis"):
        self.color_set_name = color_set_name

    @staticmethod
    def color_for_weight(weight):
        """把单个权重值映射成 RGB。"""
        w = float(weight)
        if w <= 0.0:
            return (0.0, 0.0, 0.0)
        if w >= 1.0:
            return (1.0, 1.0, 1.0)
        for i in range(len(WEIGHT_COLOR_STOPS) - 1):
            w0, c0 = WEIGHT_COLOR_STOPS[i]
            w1, c1 = WEIGHT_COLOR_STOPS[i + 1]
            if w0 <= w <= w1:
                t = (w - w0) / (w1 - w0)
                return tuple(c0[j] + (c1[j] - c0[j]) * t for j in range(3))
        return WEIGHT_COLOR_STOPS[-1][1]

    @classmethod
    def weights_to_color_array(cls, weights):
        color_array = om.MColorArray()
        for w in weights:
            r, g, b = cls.color_for_weight(w)
            color_array.append(om.MColor([r, g, b, 1.0]))
        return color_array

    def apply(self, mesh_name, weights):
        if not cmds.objExists(mesh_name):
            log.error(f"模型 '{mesh_name}' 不存在")
            return False
        color_array = self.weights_to_color_array(weights)
        return self._set_vertex_colors(mesh_name, color_array)

    def _set_vertex_colors(self, mesh_name, color_array):
        """用 setVertexColors 按顶点设色，兼容带构建历史的 mesh。"""
        color_set_name = self.color_set_name

        shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
        if not shapes:
            log.error(f"'{mesh_name}' 没有 mesh 形状")
            return False

        sel = om.MSelectionList()
        sel.add(shapes[0])
        fn = om.MFnMesh(sel.getDagPath(0))

        num_verts = fn.numVertices
        if len(color_array) != num_verts:
            log.warn(f"颜色数组长度 ({len(color_array)}) 与顶点数 ({num_verts}) 不匹配")
            return False

        if color_set_name not in fn.getColorSetNames():
            fn.createColorSet(color_set_name, True, om.MFnMesh.kRGBA)
        fn.setCurrentColorSetName(color_set_name)

        vertex_ids = om.MIntArray()
        for i in range(num_verts):
            vertex_ids.append(i)

        fn.setVertexColors(color_array, vertex_ids)
        fn.displayColors = True
        return True

    # ==================== 移除 ====================

    def remove(self, mesh_name):
        """安全删除顶点颜色集 + 清理相关历史节点。

        ★ 安全删除流程：
          1. 挂起 viewport refresh —— 避免 DG 中途重算 → PolyEngine 崩溃
          2. polyColorSet(delete=True) —— 删颜色集本身
          3. 扫描 polyColorPerVertex / createColorSet / deleteColorSet，
             按 colorSet 属性匹配；逐个用 constructionHistory=False 删除
             （避免意外级联删下游）
          4. 关闭 displayColors
          5. 恢复 refresh + 强制刷新一次

        ★ 顺序：先删 polyColorPerVertex（下游）→ 再删 createColorSet /
          deleteColorSet（上游）。
        """
        if not cmds.objExists(mesh_name):
            return False
        shapes = cmds.listRelatives(mesh_name, shapes=True, type='mesh')
        if not shapes:
            return False
        shape = shapes[0]
        color_set_name = self.color_set_name

        # 挂起 viewport 刷新
        suspended = False
        try:
            cmds.refresh(suspend=True)
            suspended = True
        except Exception:
            pass

        deleted = []
        try:
            # 1. 删颜色集本身
            cs = cmds.polyColorSet(shape, query=True, allColorSets=True) or []
            if color_set_name in cs:
                try:
                    cmds.polyColorSet(shape, delete=True, colorSet=color_set_name)
                    log.ok(f"已删除颜色集: {color_set_name}")
                except Exception as e:
                    log.warn(f"删除颜色集失败: {e}")

            # 2. 清理相关历史节点（按下游→上游顺序）
            for node_type in _HISTORY_ORDER:
                for node in cmds.ls(type=node_type) or []:
                    if not self._node_targets_set(node, color_set_name):
                        continue
                    if not cmds.objExists(node):
                        continue
                    try:
                        cmds.delete(node, constructionHistory=False)
                        deleted.append(node)
                        log.info(f"已清理历史节点: {node}")
                    except Exception as e:
                        log.warn(f"删除 {node} 失败: {e}")

            # 3. 关闭显示
            try:
                cmds.setAttr(f"{shape}.displayColors", 0)
            except Exception:
                pass

        finally:
            if suspended:
                try:
                    cmds.refresh(suspend=False)
                except Exception:
                    pass
            try:
                cmds.refresh()
            except Exception:
                pass

        if deleted:
            log.info(f"共清理 {len(deleted)} 个历史节点")
        return True

    @staticmethod
    def _node_targets_set(node, color_set_name):
        """判断节点的 colorSet / colorSetName 属性是否指向目标颜色集。"""
        for attr in ('colorSet', 'colorSetName'):
            if cmds.attributeQuery(attr, node=node, exists=True):
                try:
                    if cmds.getAttr(f"{node}.{attr}") == color_set_name:
                        return True
                except Exception:
                    pass
        return False