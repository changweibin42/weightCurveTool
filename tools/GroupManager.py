# -*- coding: utf-8 -*-
"""GroupManager —— 会话分组管理

结构：
    weightCurveJoints/
    ├── set_001/
    │   ├── joints_main_001/     ← 主 loc
    │   ├── curves_main_001/     ← 脊线曲线
    │   ├── joints_fwd_001/      ← fwd 骨骼
    │   ├── curves_fwd_001/      ← fwd 曲线
    │   ├── joints_bwd_001/      ← bwd 骨骼
    │   └── curves_bwd_001/      ← bwd 曲线
    └── set_002/
        └── ...
"""
import maya.cmds as cmds
import re

from ..Logger import log
from .MayaSelectors import curve_prefix, sanitize_name


MAIN_GROUP = "weightCurveJoints"


class GroupManager(object):

    KINDS = ("main", "fwd", "bwd")

    def __init__(self, main_group=MAIN_GROUP):
        self.main_group = main_group
        self.active_set = None
        # kind → (joints_group_full, curves_group_full)
        self.active_groups = {}

    # ==================== 查询 ====================

    def _get_next_set_number(self):
        if not cmds.objExists(self.main_group):
            return 1
        children = cmds.listRelatives(self.main_group, children=True, type='transform') or []
        nums = [int(c[4:]) for c in children
                if c.startswith("set_") and c[4:].isdigit()]
        return max(nums) + 1 if nums else 1

    def list_sets(self):
        if not cmds.objExists(self.main_group):
            return []
        children = cmds.listRelatives(self.main_group, children=True, type='transform') or []
        return sorted([c for c in children if c.startswith("set_")])

    # ==================== 创建 ====================

    def create_new_set(self):
        """新建 set_NNN，内部分 main / fwd / bwd 三组。返回 (main_joints, main_curves)"""
        if not cmds.objExists(self.main_group):
            cmds.group(empty=True, name=self.main_group)

        num = self._get_next_set_number()
        set_name = f"set_{num:03d}"
        cmds.group(empty=True, name=set_name, parent=self.main_group)
        set_full = f"{self.main_group}|{set_name}"

        self.active_set = set_full
        self.active_groups = {}

        for kind in self.KINDS:
            jn = f"joints_{kind}_{num:03d}"
            cn = f"curves_{kind}_{num:03d}"
            cmds.group(empty=True, name=jn, parent=set_full)
            cmds.group(empty=True, name=cn, parent=set_full)
            self.active_groups[kind] = (
                f"{set_full}|{jn}",
                f"{set_full}|{cn}",
            )

        log.info(f"新建会话: {set_name}（含 main / fwd / bwd）")
        return self.active_groups["main"]

    # ==================== 读取 ====================

    def get_joints_from_active(self, kind='main'):
        if kind not in self.active_groups:
            return []
        jg, _ = self.active_groups[kind]
        return self._get_joints_from_group(jg)

    def get_joints_from_set(self, set_name=None, kind='main'):
        jg = self._resolve_group(set_name, kind, 'joints')
        return self._get_joints_from_group(jg)

    def get_curves_from_set(self, set_name=None, kind='main'):
        cg = self._resolve_group(set_name, kind, 'curves')
        if not cg or not cmds.objExists(cg):
            return []
        return cmds.listRelatives(cg, children=True, type='transform') or []

    def get_main_locs_from_joint_set(self, jnt):
        """从 jnt 反推它所在 set 的 main locs（排除 _fwd / _bwd 后缀）。

        路径：jnt → joints_xxx_NNN → set_NNN → joints_main_NNN → 其中的 loc

        参数：
            jnt  该 set 里任意一个 joint（主 / fwd / bwd 都行）
        返回：
            该 set 里的 main loc 列表；找不到则 []
        """
        pars = cmds.listRelatives(jnt, parent=True, type='transform') or []
        if not pars:
            return []
        jg = pars[0]
        pars2 = cmds.listRelatives(jg, parent=True, type='transform') or []
        if not pars2:
            return []
        set_group = pars2[0]
        children = cmds.listRelatives(set_group, children=True, type='transform') or []
        for c in children:
            if c.startswith("joints_main_"):
                mg = f"{set_group}|{c}"
                return self._get_joints_from_group(mg)
        return []

    def _get_joints_from_group(self, jg):
        if not jg or not cmds.objExists(jg):
            return []
        children = cmds.listRelatives(jg, children=True, type='transform') or []
        return [c for c in children if cmds.nodeType(c) == 'joint']

    def _resolve_group(self, set_name, kind, group_type):
        if set_name is None:
            if kind in self.active_groups:
                jg, cg = self.active_groups[kind]
                return jg if group_type == 'joints' else cg
            return None

        if "|" in set_name:
            set_full = set_name
        elif set_name.startswith("set_"):
            set_full = f"{self.main_group}|{set_name}"
        else:
            return None

        if not cmds.objExists(set_full):
            return None

        m = re.search(r'set_(\d+)', set_full)
        if not m:
            return None
        num = m.group(1)

        target = f"{set_full}|{group_type}_{kind}_{num}"
        return target if cmds.objExists(target) else None

    # ==================== 反推 mesh ====================

    def find_mesh_from_active(self):
        """从 active_groups 的 curves 组反推 mesh 名。

        遍历 main / fwd / bwd 三组，找第一个非 _fwd / _bwd 结尾的
        *_ring_* 曲线，从它名字截出 mesh 名。

        返回 mesh 名（str）或 None。
        """
        for kind in self.KINDS:
            if kind not in self.active_groups:
                continue
            _, cg = self.active_groups[kind]
            if not cg or not cmds.objExists(cg):
                continue
            curves = cmds.listRelatives(cg, children=True, type='transform') or []
            for crv_name in curves:
                if "_ring_" not in crv_name:
                    continue
                crv_short = crv_name.split('|')[-1]
                base = crv_short.split("_ring_")[0]
                if base.endswith("_fwd") or base.endswith("_bwd"):
                    continue
                return base
        return None

    # ==================== 移动 ====================

    def move_joint_to_active(self, jnt, kind='main'):
        if kind not in self.active_groups:
            return False
        jg, _ = self.active_groups[kind]
        if not jg or not cmds.objExists(jg):
            return False
        try:
            cmds.parent(jnt, jg)
            return True
        except Exception as e:
            log.warn(f"移动 {jnt} 到 {jg} 失败: {e}")
            return False

    @staticmethod
    def _move_curves(mesh_name, kind, group):
        prefix = curve_prefix(mesh_name, kind)
        for suffix in ("_ring_laplacian", "_ring_gaussian"):
            name = f"{prefix}{suffix}"
            if cmds.objExists(name):
                try:
                    cmds.parent(name, group)
                except Exception:
                    pass

    def move_curves_to_active(self, mesh_name, kind='main'):
        if kind not in self.active_groups:
            return
        _, cg = self.active_groups[kind]
        if not cg or not cmds.objExists(cg):
            return
        self._move_curves(mesh_name, kind, cg)

    def move_curves_to_set(self, set_name, mesh_name, kind='main'):
        cg = self._resolve_group(set_name, kind, 'curves')
        if not cg or not cmds.objExists(cg):
            return
        self._move_curves(mesh_name, kind, cg)

    # ==================== 按 jnt 反查 kind ====================

    def find_kind_by_joint(self, jnt):
        """根据 jnt 所在组名解析 kind，返回 'main' / 'fwd' / 'bwd' / None"""
        pars = cmds.listRelatives(jnt, parent=True, type='transform') or []
        if not pars:
            return None
        jg = pars[0]
        m = re.match(r'joints_(\w+?)_\d+$', jg)
        if not m:
            return None
        kind = m.group(1)
        return kind if kind in self.KINDS else None

    def move_curves_to_set_by_joint(self, jnt, mesh_name):
        """根据 jnt 所在组反查 set + kind，把对应曲线移进去"""
        pars = cmds.listRelatives(jnt, parent=True, type='transform') or []
        if not pars:
            return
        jg = pars[0]
        m = re.match(r'joints_(\w+?)_(\d+)$', jg)
        if not m:
            return
        kind, num = m.group(1), m.group(2)
        if kind not in self.KINDS:
            return

        set_full = f"{self.main_group}|set_{num}"
        cg = f"{set_full}|curves_{kind}_{num}"
        if not cmds.objExists(cg):
            return

        self._move_curves(mesh_name, kind, cg)

    # ==================== 清理 ====================

    def clear_all(self):
        if cmds.objExists(self.main_group):
            try:
                cmds.delete(self.main_group)
            except Exception:
                pass
        self.active_set = None
        self.active_groups = {}