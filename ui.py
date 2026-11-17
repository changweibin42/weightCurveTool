# -*- coding: utf-8 -*-
"""UI 主窗口（曲线边界 / 骨骼投影 双 Tab）"""
import maya.cmds as cmds

try:
    from PySide2.QtWidgets import *
    from PySide2.QtCore import *
    from PySide2.QtGui import *
except ImportError:
    from PySide.QtGui import *
    from PySide.QtCore import *

from . import core
from .Logger import log
from .ui_widgets import (
    HoverButton,
    WeightCurveWidget,
    WeightParamGroupMulti,
    WeightParamGroup,
    FloatSliderGroup,
)


WINDOW_OBJ_NAME = "WeightCurveEditorWindow"


class WeightCurveEditorWindow(QWidget):
    STATUS_INFO = "#9ecbff"
    STATUS_OK = "#8ce08c"
    STATUS_WARN = "#ffd479"
    STATUS_ERROR = "#ff8c8c"

    BTN_TIPS = {
        "create": "在模型表面连续点击创建 loc 骨骼。点回首节点闭合，或按 Q 保留开放曲线并退出。",
        "modify": "十字光标拖动骨骼。松手后自动重建对应曲线；拖动主 loc 会同时重绘顶点颜色。",
        "boundary": "在模型表面点击一次，点击侧将作为 fwd 侧。点击位置到脊线的距离决定边界滑动量。",
        "mode": "在 Soft（对称衰减）和 IK（方向性渐变）之间切换。ik模式需要生成边界才能看到差异。",
        "reset": "删除所有 set 分组 + 顶点颜色，回到初始状态。",
        "redraw": "用当前 UI 曲线参数重算顶点权重并刷新顶点颜色。",
    }

    TIP_SELECT_MODEL = "点击读取当前 Maya 选中的 Mesh 模型（可先在视口选中）。"
    TIP_SELECT_BONE = "点击读取当前 Maya 选中的 Joint 骨骼（要被刷权重的对象）。"
    TIP_APPLY_XS = "把左侧四个输入框的 Xs 值应用到上方贝塞尔曲线。"
    TIP_APPLY_YS = "把左侧四个输入框的 Ys 值应用到上方贝塞尔曲线。"
    TIP_MIRROR = ("开启后贝塞尔手柄关于 (0.5, 0.5) 中心对称：\n"
                  "拖动 p1 → p2 自动镜像为 (1-x1, 1-y1)；反之亦然。\n"
                  "应用 Xs 时强制 p2.x = 1 - p1.x；应用 Ys 时强制 p2.y = 1 - p1.y。")

    LEGACY_MODES = [
        ("次级", "soft"),
        ("关节", "ik"),
        ("脊椎", "spine"),
        ("背带", "belt"),
        ("眉毛", "brow"),
        ("裙子", "cloth"),
        ("眼皮", "eye"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setObjectName(WINDOW_OBJ_NAME)
        self.setWindowTitle("权重曲线编辑器")
        self.setMinimumSize(540, 500)
        self.setMaximumWidth(640)
        self.setStyleSheet("background-color: rgb(43, 43, 43);")

        self._last_mesh = None
        self._current_status = ("", self.STATUS_INFO)

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(100)
        self._repaint_timer.timeout.connect(self._on_repaint_timeout)

        self._legacy_closed=False
        self._legacy_solving=False
        self._legacy_pending=False
        self._legacy_syncing_radius=False
        self._legacy_last_maya_radius=None
        self._legacy_timer=QTimer(self)
        self._legacy_timer.setSingleShot(True)
        self._legacy_timer.setInterval(120)
        self._legacy_timer.timeout.connect(self._on_legacy_timeout)
        self._legacy_radius_poll=QTimer(self)
        self._legacy_radius_poll.setInterval(150)
        self._legacy_radius_poll.timeout.connect(self._sync_legacy_radius_from_maya)

        # 外层：QScrollArea 兜底
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background-color: rgb(43, 43, 43); }"
        )
        outer.addWidget(self._scroll)

        content = QWidget()
        content.setStyleSheet("background-color: rgb(43, 43, 43);")
        self._scroll.setWidget(content)

        main_layout = QVBoxLayout(content)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # 模型 / 骨骼
        self.model_group = WeightParamGroup(
            "模型", "", "选择", self.TIP_SELECT_MODEL)
        self.model_group.set_hover_callbacks(
            self._on_button_hover, self._on_button_leave)
        self.model_group.on_clicked(lambda: self.on_select("模型"))
        main_layout.addLayout(self.model_group)

        self.bone_group = WeightParamGroup(
            "骨骼", "", "选择", self.TIP_SELECT_BONE)
        self.bone_group.set_hover_callbacks(
            self._on_button_hover, self._on_button_leave)
        self.bone_group.on_clicked(lambda: self.on_select("骨骼"))
        main_layout.addLayout(self.bone_group)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setFrameShadow(QFrame.Sunken)
        sep1.setStyleSheet("background-color: #555;")
        main_layout.addWidget(sep1)

        # 贝塞尔曲线（共享）
        self.curve_widget = WeightCurveWidget()
        main_layout.addWidget(self.curve_widget, alignment=Qt.AlignCenter)

        # 镜像手柄 checkbox
        self.mirror_checkbox = QCheckBox(
            "镜像贝塞尔手柄（p1 / p2 关于 (0.5, 0.5) 中心对称）")
        self.mirror_checkbox.setStyleSheet(
            "color: #9ecbff; font-size: 10pt; padding: 2px;")
        self.mirror_checkbox.setToolTip(self.TIP_MIRROR)
        self.mirror_checkbox.stateChanged.connect(self._on_mirror_toggled)
        main_layout.addWidget(self.mirror_checkbox)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        sep2.setStyleSheet("background-color: #555;")
        main_layout.addWidget(sep2)

        # Xs / Ys（共享）
        self.xs_group = WeightParamGroupMulti(
            "Xs 控制点", "应用", self.TIP_APPLY_XS)
        self.xs_group.set_hover_callbacks(
            self._on_button_hover, self._on_button_leave)
        self.xs_group.on_clicked(self.on_apply_xs)
        main_layout.addLayout(self.xs_group)

        self.ys_group = WeightParamGroupMulti(
            "Ys 控制点", "应用", self.TIP_APPLY_YS)
        self.ys_group.set_hover_callbacks(
            self._on_button_hover, self._on_button_leave)
        self.ys_group.on_clicked(self.on_apply_ys)
        main_layout.addLayout(self.ys_group)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setFrameShadow(QFrame.Sunken)
        sep3.setStyleSheet("background-color: #555;")
        main_layout.addWidget(sep3)

        # 双 Tab
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #555; }
            QTabBar::tab {
                background: #3a3a3a;
                color: white;
                padding: 12px 28px;
                min-width: 100px;
                min-height: 22px;
                font-size: 11pt;
            }
            QTabBar::tab:selected { background: #5a5a5a; }
            QTabBar::tab:hover { background: #4a4a4a; }
        """)
        self.tabs.tabBar().setMinimumHeight(34)
        main_layout.addWidget(self.tabs)

        self.curve_tab = self._build_curve_tab()
        self.tabs.addTab(self.curve_tab, "曲线边界")

        self.legacy_tab = self._build_legacy_tab()
        self.tabs.addTab(self.legacy_tab, "骨骼投影")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        # 状态栏
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setFixedHeight(90)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.status_label.setStyleSheet(
            "color: #9ecbff; font-size: 11pt; "
            "padding: 8px; background-color: #2a2a2a; "
            "border: 1px solid #444; border-radius: 4px;"
        )
        main_layout.addWidget(self.status_label)

        # 信号
        self.curve_widget.valueChanged.connect(self.on_curve_changed)
        self.on_curve_changed()

        self.set_status(
            "💡 请先选择模型和骨骼，再点「创建曲线」或在「骨骼投影」tab 里点「解算」",
            self.STATUS_INFO)

        # 打开时先给初始尺寸，实际高度由 _on_tab_changed 按内容精确调整
        self.resize(580, 950)
        QTimer.singleShot(0, lambda: self._on_tab_changed(self.tabs.currentIndex()))

    # ==================== Tab 构建 ====================

    def _build_curve_tab(self):
        """曲线边界 tab：紧凑布局，无 stretch。"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(2, 6, 2, 6)
        layout.setSpacing(8)

        current_mode = core.get_weight_mode()
        display_mode = "Soft" if current_mode == "soft" else "IK"
        btn_defs = [
            ("创建曲线", "create", self.on_create_curve),
            ("修改曲线", "modify", self.on_modify_curve),
            ("生成边界", "boundary", self.on_generate_boundaries),
            (f"模式: {display_mode}", "mode", self.on_toggle_mode),
            ("重置颜色", "reset", self.on_reset_colors),
            ("重绘颜色", "redraw", self.on_redraw_weight_colors),
        ]
        grid = QGridLayout()
        grid.setSpacing(8)
        self.action_buttons = []
        for idx, (text, key, cb) in enumerate(btn_defs):
            tip = self.BTN_TIPS.get(key, "")
            btn = HoverButton(text, tip)
            btn.setStyleSheet(
                "background-color: #5a5a5a; color: white; font-size: 11pt;")
            btn.set_hover_callbacks(
                self._on_button_hover, self._on_button_leave)
            btn.clicked.connect(cb)
            grid.addWidget(btn, idx // 3, idx % 3)
            self.action_buttons.append(btn)
        layout.addLayout(grid)
        return w

    def _build_legacy_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(4, 10, 4, 10)
        layout.setSpacing(10)

        # 7 种模式
        mode_label = QLabel("模式（7 选 1）：")
        mode_label.setStyleSheet(
            "color: #FFD700; font-size: 12pt; font-weight: bold;")
        layout.addWidget(mode_label)

        mode_grid = QGridLayout()
        mode_grid.setSpacing(8)
        self.legacy_mode_group = QButtonGroup(self)
        for i, (text, mode) in enumerate(self.LEGACY_MODES):
            radio = QRadioButton(text)
            radio.setStyleSheet("color: white; font-size: 11pt;")
            radio.setProperty("mode_id", mode)
            self.legacy_mode_group.addButton(radio, i)
            mode_grid.addWidget(radio, i // 4, i % 4)
        self.legacy_mode_group.button(0).setChecked(True)
        layout.addLayout(mode_grid)

        range_row=QHBoxLayout()
        range_row.addWidget(QLabel("关节/脊椎范围："))
        self.legacy_range_mode=QComboBox()
        self.legacy_range_mode.addItem("自动范围（网格表面连通）", "auto")
        self.legacy_range_mode.addItem("当前顶点/软选范围", "selection")
        range_row.addWidget(self.legacy_range_mode)
        preview=QPushButton("预览范围（仅选点）")
        preview.clicked.connect(self.on_preview_legacy_range)
        range_row.addWidget(preview)
        layout.addLayout(range_row)

        reference_row=QHBoxLayout()
        reference_row.addWidget(QLabel("关节/脊椎参考："))
        self.legacy_reference_space=QComboBox()
        self.legacy_reference_space.addItem("Bind 绑定空间（推荐）", "bind")
        self.legacy_reference_space.addItem("当前姿态（原方式）", "current")
        self.legacy_reference_space.setToolTip("关节/脊椎在绑定输入中计算，再按顶点编号写回当前姿态；次级仍使用当前 Maya 软选。")
        reference_row.addWidget(self.legacy_reference_space)
        layout.addLayout(reference_row)

        # 实时开关
        self.legacy_realtime = QCheckBox("实时（拖动贝塞尔/改半径立即重算）")
        self.legacy_realtime.setStyleSheet("color: white; font-size: 11pt;")
        layout.addWidget(self.legacy_realtime)

        # 半径
        radius_row = QHBoxLayout()
        radius_label = QLabel("软选半径：")
        radius_label.setStyleSheet("color: white; font-size: 11pt;")
        radius_label.setFixedWidth(80)
        self.legacy_radius = FloatSliderGroup()
        self.legacy_radius.spin.setDecimals(6)
        self.legacy_radius.set_range(0.0,20.0)
        self._sync_legacy_radius_from_maya()
        radius_row.addWidget(radius_label)
        radius_row.addLayout(self.legacy_radius)
        layout.addLayout(radius_row)

        # 说明
        hint = QLabel(
            "· 次级 (soft)：需先在 Maya 中选中顶点作为软选择中心\n"
            "  source 手动解锁；pick 按需临时解锁，仅当前软选范围参与\n"
            "· 关节 (ik)：先将原 pick 退 parent，再在新范围分配；范围外旧 pick 清除\n"
            "· 脊椎 (spine)：用户解锁的蒙皮 joint 必须组成一条单链，无需 pick 链头\n"
            "  脊椎不改 liw；关节/脊椎不再固定使用 X 轴\n"
            "· 背带/眉毛 (belt/brow)：仍沿用原链头逻辑，后续单独处理\n"
            "· 裙子 (cloth)：所有未锁 joint\n"
            "· 眼皮 (eye)：≥1 个未锁 joint，自动 BFS 分配顶点")
        hint.setStyleSheet(
            "color: #aaa; font-size: 10pt; "
            "padding: 6px; background-color: #2f2f2f; "
            "border: 1px solid #444; border-radius: 4px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 解算按钮
        self.legacy_solve_btn = HoverButton(
            "解算",
            "用当前参数对选中 mesh 计算骨骼投影权重。")
        self.legacy_solve_btn.setStyleSheet(
            "background-color: #5a5a5a; color: white; "
            "font-size: 12pt; padding: 8px;")
        self.legacy_solve_btn.set_hover_callbacks(
            self._on_button_hover, self._on_button_leave)
        self.legacy_solve_btn.clicked.connect(self.on_legacy_solve)
        layout.addWidget(self.legacy_solve_btn)

        layout.addStretch()

        # 信号
        self.legacy_realtime.stateChanged.connect(
            self._on_legacy_realtime_toggle)
        self.legacy_radius.valueChange.connect(self._on_legacy_radius_changed)
        self.legacy_reference_space.currentIndexChanged.connect(self._on_legacy_reference_changed)
        self.legacy_range_mode.currentIndexChanged.connect(self._on_legacy_reference_changed)
        self.legacy_mode_group.buttonClicked.connect(
            lambda _: self._on_legacy_realtime_toggle(
                self.legacy_realtime.checkState()))

        return w

    # ==================== 状态栏 ====================

    def _apply_status(self, text, color):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 11pt; "
            f"padding: 8px; background-color: #2a2a2a; "
            f"border: 1px solid #444; border-radius: 4px;"
        )

    def set_status(self, text, color=None, log_fn=None):
        if color is None:
            color = self.STATUS_INFO
        self._current_status = (text, color)
        self._apply_status(text, color)
        if log_fn is not None:
            log_fn(text)

    def _on_button_hover(self, tooltip_text):
        if not tooltip_text:
            return
        self._apply_status(f"💡 {tooltip_text}", self.STATUS_WARN)

    def _on_button_leave(self):
        text, color = self._current_status
        self._apply_status(text, color)

    def _on_tab_changed(self, index):
        """切换 tab 时按当前 page 内容调整 tabs 高度 + 窗口高度。"""
        page = self.tabs.widget(index)
        if page is None:
            return
        tab_bar_h = self.tabs.tabBar().sizeHint().height()
        page_h = page.sizeHint().height()
        tabs_h = page_h + tab_bar_h + 4
        self.tabs.setFixedHeight(tabs_h)
        QTimer.singleShot(0, self._fit_window_to_content)

    def _fit_window_to_content(self):
        """窗口高度刚好包裹内容，不超屏幕。"""
        content = self._scroll.widget()
        if content is None:
            return
        need_h = content.sizeHint().height() + 4
        try:
            screen = QApplication.instance().primaryScreen().availableGeometry()
            max_h = int(screen.height() * 0.92)
        except Exception:
            max_h = 2000
        new_h = min(need_h, max_h)
        new_h = max(new_h, 500)
        self.resize(self.width(), new_h)

    # ==================== 镜像开关 ====================

    def _on_mirror_toggled(self, state):
        """镜像开关切换。开启时立即强制 p1/p2 中心对称。"""
        enabled = (state == Qt.Checked)
        self.curve_widget.set_mirror(enabled)
        if enabled:
            self.set_status(
                "✅ 镜像已开启：p1 / p2 关于 (0.5, 0.5) 中心对称",
                self.STATUS_OK)
        else:
            self.set_status("✅ 镜像已关闭", self.STATUS_INFO)

    # ==================== 辅助 ====================

    def _update_xs_display(self):
        pts = self.curve_widget.get_curve_data()
        self.xs_group.set_values([p[0] for p in pts])

    def _update_ys_display(self):
        pts = self.curve_widget.get_curve_data()
        self.ys_group.set_values([p[1] for p in pts])

    def _get_current_xs_ys(self):
        pts = self.curve_widget.get_curve_data()
        return [p[0] for p in pts], [p[1] for p in pts]

    def _select_working_mesh(self):
        mesh = self._last_mesh or self.model_group.get_text().strip() or None
        if not mesh or not cmds.objExists(mesh):
            return
        try:
            cmds.select(mesh, replace=True)
        except Exception:
            pass

    def _require_picked_joint(self):
        """只检查 picked 存在。par 可选（独立骨骼场景 par 为 None 是正常的）。"""
        picked, par = core.get_reference_joint()
        if not picked or not cmds.objExists(picked):
            self.set_status(
                "❌ 请先在 UI「骨骼」里拾取要刷权重的骨骼（点「选择」）",
                self.STATUS_ERROR, log_fn=log.warn)
            return None
        return picked, par

    # ==================== 贝塞尔自动重绘（曲线边界 tab） ====================

    def _on_repaint_timeout(self):
        if not self._last_mesh:
            return
        if not cmds.objExists(self._last_mesh):
            return
        try:
            xs, ys = self._get_current_xs_ys()
            core.update_combined_vertex_colors(self._last_mesh, xs=xs, ys=ys)
            self.set_status("🎨 贝塞尔已更新，自动重绘中", self.STATUS_INFO)
        except Exception as e:
            log.warn(f"自动重绘失败: {e}")

    # ==================== 回调：选择 ====================

    def on_select(self, target):
        if target == "模型":
            obj = core.get_selected_mesh_transform()
            if obj:
                self.model_group.set_text(obj)
                self._last_mesh = obj
                self.set_status(f"✅ 已选中模型：{obj}", self.STATUS_OK)
            else:
                self.set_status(
                    "⚠️ 未选中有效的 Mesh 模型",
                    self.STATUS_WARN, log_fn=log.warn)
        else:
            mesh_text = self.model_group.get_text().strip() or None
            obj, reason = core.pick_reference_joint(mesh_name=mesh_text)
            if obj:
                self.bone_group.set_text(obj)
                self.set_status(f"✅ 已选中骨骼：{obj}", self.STATUS_OK)
            else:
                self.set_status(f"⚠️ 拾取骨骼失败：{reason}",
                                self.STATUS_WARN, log_fn=log.warn)

    # ==================== 回调：贝塞尔 / Xs / Ys ====================

    def on_curve_changed(self):
        self._update_xs_display()
        self._update_ys_display()
        xs, ys = self._get_current_xs_ys()
        core.set_curve_points(xs, ys)

        if self.tabs.currentIndex() == 0:
            if self._last_mesh:self._repaint_timer.start()
        else:
            self._queue_legacy_solve()

    def on_apply_xs(self):
        vals = self.xs_group.get_values()
        if vals is None:
            self.set_status("❌ Xs 输入格式错误",
                            self.STATUS_ERROR, log_fn=log.warn)
            return
        pts = self.curve_widget.get_curve_data()

        if self.curve_widget.mirror_enabled:
            # 镜像模式：只信任 p1.x，p2.x = 1 - p1.x
            x1 = vals[1]
            pts[0][0] = vals[0]
            pts[1][0] = x1
            pts[2][0] = 1.0 - x1
            pts[3][0] = vals[3]
            self.curve_widget.set_curve_data(*pts)
            self.set_status(
                f"✅ Xs 已应用（镜像）：p1.x={x1:.3f}, p2.x={1.0 - x1:.3f}",
                self.STATUS_OK)
        else:
            for i in range(4):
                pts[i][0] = vals[i]
            self.curve_widget.set_curve_data(*pts)
            self.set_status(f"✅ Xs 已应用：{vals}", self.STATUS_OK)

    def on_apply_ys(self):
        vals = self.ys_group.get_values()
        if vals is None:
            self.set_status("❌ Ys 输入格式错误",
                            self.STATUS_ERROR, log_fn=log.warn)
            return
        pts = self.curve_widget.get_curve_data()

        if self.curve_widget.mirror_enabled:
            # 镜像模式：只信任 p1.y，p2.y = 1 - p1.y
            y1 = vals[1]
            pts[0][1] = vals[0]
            pts[1][1] = y1
            pts[2][1] = 1.0 - y1
            pts[3][1] = vals[3]
            self.curve_widget.set_curve_data(*pts)
            self.set_status(
                f"✅ Ys 已应用（镜像）：p1.y={y1:.3f}, p2.y={1.0 - y1:.3f}",
                self.STATUS_OK)
        else:
            for i in range(4):
                pts[i][1] = vals[i]
            self.curve_widget.set_curve_data(*pts)
            self.set_status(f"✅ Ys 已应用：{vals}", self.STATUS_OK)

    # ==================== 回调：曲线边界 tab ====================

    def on_create_curve(self):
        mesh_name = self.model_group.get_text().strip()
        direction_joint = self.bone_group.get_text().strip()
        if not mesh_name:
            self.set_status("❌ 请填写或选择模型名称",
                            self.STATUS_ERROR, log_fn=log.warn)
            return
        if not direction_joint:
            self.set_status("❌ 请填写或选择方向骨骼名称",
                            self.STATUS_ERROR, log_fn=log.warn)
            return
        if self._require_picked_joint() is None:
            return

        self._last_mesh = mesh_name
        xs, ys = self._get_current_xs_ys()
        core.set_curve_points(xs, ys)

        core.ensure_rest_surface(mesh_name)
        self._select_working_mesh()
        core.init_weights_for_picked(mesh_name)

        self.set_status(
            "🎯 已进入十字光标：连续点击创建 loc；点回首节点闭合，或按 Q 退出",
            self.STATUS_INFO)
        core.start_continuous_surface_locator(mesh_name, direction_joint)

    def on_modify_curve(self):
        if self._require_picked_joint() is None:
            return
        mesh_name = self._last_mesh or self.model_group.get_text().strip()
        if mesh_name:
            core.ensure_rest_surface(mesh_name)
        self._select_working_mesh()
        self.set_status("🎯 已进入修改模式：拖动骨骼，松手重绘",
                        self.STATUS_INFO)
        core.start_modify_curve_mode()

    def on_generate_boundaries(self):
        mesh_name = self.model_group.get_text().strip()
        if not mesh_name:
            self.set_status("❌ 请先选择模型",
                            self.STATUS_ERROR, log_fn=log.warn)
            return
        if self._require_picked_joint() is None:
            return
        ridge = f"{mesh_name}_ring_laplacian"
        if not cmds.objExists(ridge):
            self.set_status(
                f"❌ 未找到脊线「{ridge}」", self.STATUS_ERROR, log_fn=log.warn)
            return
        core.ensure_rest_surface(mesh_name)
        self._select_working_mesh()
        self.set_status(
            "🎯 已进入边界模式：点击一次，点击侧 = fwd 侧",
            self.STATUS_INFO)
        core.start_boundary_pick_mode(mesh_name)

    def on_redraw_weight_colors(self):
        if not self._last_mesh:
            self.set_status(
                "❌ 尚未创建任何骨骼", self.STATUS_ERROR, log_fn=log.warn)
            return
        if self._require_picked_joint() is None:
            return
        self._select_working_mesh()
        xs, ys = self._get_current_xs_ys()
        mesh_short = self._last_mesh.split('|')[-1].split(':')[-1]
        fwd_crv = f"{mesh_short}_fwd_ring_laplacian"
        bwd_crv = f"{mesh_short}_bwd_ring_laplacian"
        mode = core.get_weight_mode()
        if cmds.objExists(fwd_crv) and cmds.objExists(bwd_crv):
            self.set_status(
                f"🎨 重绘中（边界模式，{mode.upper()}）", self.STATUS_INFO)
        else:
            self.set_status("🎨 重绘中（软选择模式）", self.STATUS_INFO)

        core.update_combined_vertex_colors(self._last_mesh, xs=xs, ys=ys)
        core.ensure_rest_surface(self._last_mesh)
        self._select_working_mesh()

    def on_toggle_mode(self):
        if self._require_picked_joint() is None:
            return
        current = core.get_weight_mode()
        new_mode = "ik" if current == "soft" else "soft"
        core.set_weight_mode(new_mode)
        display = "Soft" if new_mode == "soft" else "IK"
        self.action_buttons[3].setText(f"模式: {display}")
        self.action_buttons[3].update_hover_text(
            f"点击切换到 {'IK' if new_mode == 'soft' else 'Soft'} 模式。"
            f"需要先生成边界才能看到差异。")
        if not self._last_mesh:
            self.set_status(f"✅ 已切换 {display}", self.STATUS_WARN)
            return

        core.ensure_rest_surface(self._last_mesh)

        # liw 由 update_combined_vertex_colors 写入前自动检查，这里不重复检测
        self._select_working_mesh()
        xs, ys = self._get_current_xs_ys()
        core.update_combined_vertex_colors(self._last_mesh, xs=xs, ys=ys)
        self._select_working_mesh()

    def on_reset_colors(self):
        mesh_name = self.model_group.get_text().strip()
        if not mesh_name:
            self.set_status("❌ 请先选择模型",
                            self.STATUS_ERROR, log_fn=log.warn)
            return
        try:
            self._repaint_timer.stop()
        except Exception:
            pass
        core.reset_weight_history(mesh_name)
        try:
            cmds.select(clear=True)
        except Exception:
            pass
        self.set_status(f"✅ 已重置（{mesh_name}）", self.STATUS_OK)

    # ==================== 回调：骨骼投影 tab ====================

    def _sync_legacy_radius_from_maya(self):
        if self._legacy_closed:return
        try:
            radius=float(cmds.softSelect(q=True,softSelectDistance=True))
        except Exception:return
        if not (0.<=radius<float('inf')):return
        previous=self._legacy_last_maya_radius
        changed=previous is None or abs(radius-previous)>max(1e-9,abs(radius)*1e-9)
        if not changed:return
        self._legacy_syncing_radius=True
        try:
            # Block both child controls, since FloatSliderGroup emits from its
            # own handlers rather than QObject.blockSignals on the layout.
            spin=self.legacy_radius.spin;slider=self.legacy_radius.slider
            a=spin.blockSignals(True);b=slider.blockSignals(True)
            try:
                maximum=max(20.,radius*2.,float(spin.maximum()))
                self.legacy_radius.set_range(0.,maximum)
                spin.setValue(radius);slider.setValue(int(round(radius*1000)))
            finally:
                spin.blockSignals(a);slider.blockSignals(b)
            self._legacy_last_maya_radius=radius
        finally:self._legacy_syncing_radius=False
        if previous is not None:self._queue_legacy_solve()

    def _queue_legacy_solve(self):
        if self._legacy_closed or not self.legacy_realtime.isChecked() or self.tabs.currentIndex()!=1:return
        if not (self._last_mesh or self.model_group.get_text().strip()):return
        if self._legacy_solving:self._legacy_pending=True
        else:self._legacy_timer.start()

    def _on_legacy_timeout(self):
        if self._legacy_closed or not self.isVisible():return
        if not self.legacy_realtime.isChecked() or self.tabs.currentIndex()!=1:return
        self.on_legacy_solve()

    def _on_legacy_reference_changed(self,*args):
        self._queue_legacy_solve()

    def closeEvent(self,event):
        self._legacy_closed=True;self._legacy_pending=False
        self._legacy_timer.stop();self._legacy_radius_poll.stop();self._repaint_timer.stop()
        super().closeEvent(event)

    def showEvent(self,event):
        self._legacy_closed=False
        self._legacy_radius_poll.start()
        self._sync_legacy_radius_from_maya()
        super().showEvent(event)

    def _on_legacy_realtime_toggle(self,state):
        if state:self._queue_legacy_solve()
        else:
            self._legacy_timer.stop();self._legacy_pending=False

    def _on_legacy_radius_changed(self,value):
        if self._legacy_syncing_radius or self._legacy_closed:return
        radius=float(value)
        self._legacy_syncing_radius=True
        try:
            current=float(cmds.softSelect(q=True,softSelectDistance=True))
            if abs(radius-current)>max(1e-9,abs(radius)*1e-9):
                cmds.softSelect(softSelectDistance=radius)
            self._legacy_last_maya_radius=float(cmds.softSelect(q=True,softSelectDistance=True))
        finally:self._legacy_syncing_radius=False
        self._queue_legacy_solve()

    def on_preview_legacy_range(self):
        mesh=self._last_mesh or self.model_group.get_text().strip()
        idx=self.legacy_mode_group.checkedId()
        mode=self.legacy_mode_group.button(idx).property("mode_id") if idx>=0 else None
        picked,_=core.get_reference_joint()
        range_mode=self.legacy_range_mode.itemData(self.legacy_range_mode.currentIndex())
        try:
            plan=core.preview_legacy_range(mode,mesh,target_joint=picked,range_mode=range_mode,
                                          r=1.,return_plan=True,reference_space=self.legacy_reference_space.itemData(self.legacy_reference_space.currentIndex()))
            ids=plan['apply_vertices']
            if range_mode=='auto':
                # Selection changes are explicit only in this preview action.
                cmds.select(["{}.vtx[{}]".format(mesh,v) for v in ids],replace=True)
            # Replacing hard selection in selection mode could expand its
            # subsequent soft selection. Leave that user's selection intact.
            message=("新分配范围：{} 点；范围外退回 parent：{} 点；未写权重".format(
                len(ids),len(plan['reset_vertices'])) if mode=='ik'
                else "范围内可写顶点：{}；未写权重".format(len(ids)))
            self.set_status(message,self.STATUS_OK)
        except Exception as exc:
            self.set_status(str(exc),self.STATUS_WARN,log_fn=log.warn)

    def on_legacy_solve(self):
        if self._legacy_closed:return
        if self._legacy_solving:
            self._legacy_pending=True;return
        self._legacy_timer.stop();self._legacy_pending=False;self._legacy_solving=True
        try:self._solve_legacy_once()
        finally:
            self._legacy_solving=False
            if self._legacy_pending:
                self._legacy_pending=False;self._queue_legacy_solve()

    def _solve_legacy_once(self):
        mesh = self._last_mesh or self.model_group.get_text().strip()
        if not mesh:
            self.set_status("❌ 请先选择模型",
                            self.STATUS_ERROR, log_fn=log.warn)
            return
        if not cmds.objExists(mesh):
            self.set_status(
                f"❌ 模型 '{mesh}' 不存在", self.STATUS_ERROR, log_fn=log.warn)
            return

        skin = core.find_skin_cluster(mesh)
        if not skin:
            self.set_status(
                f"❌ '{mesh}' 没有 skinCluster（骨骼投影需要先蒙皮）",
                self.STATUS_ERROR, log_fn=log.warn)
            return

        idx = self.legacy_mode_group.checkedId()
        if idx < 0:
            return
        mode = self.legacy_mode_group.button(idx).property("mode_id")

        picked, par = core.get_reference_joint()

        # 需要 UI 拾取骨骼的模式
        need_picked_modes = ('soft', 'ik', 'brow', 'belt', 'cloth')
        if mode in need_picked_modes:
            if not picked or not cmds.objExists(picked):
                mode_cn = {
                    'soft': '次级',
                    'ik': '关节',
                    'spine': '脊椎',
                    'brow': '眉毛',
                    'belt': '背带',
                    'cloth': '裙子',
                }.get(mode, mode)
                self.set_status(
                    f"❌ {mode_cn} 模式需要先在 UI「骨骼」里拾取骨骼",
                    self.STATUS_ERROR, log_fn=log.warn)
                return

        xs, ys_display = self._get_current_xs_ys()
        ys = [1.0 - y for y in ys_display]
        # The control edits Maya radius itself; never multiply by it again.
        r = 1.0

        ok = core.paint_legacy(mode, mesh, xs=xs, ys=ys, r=r,
                               target_joint=picked,
                               range_mode=self.legacy_range_mode.itemData(self.legacy_range_mode.currentIndex()),
                               reference_space=self.legacy_reference_space.itemData(self.legacy_reference_space.currentIndex()))
        if ok:
            self.set_status(
                f"✅ 骨骼投影 ({mode}) 已解算到 {mesh}", self.STATUS_OK)
        else:
            self.set_status(
                f"⚠️ 骨骼投影 ({mode}) 解算失败（详见脚本编辑器）",
                self.STATUS_WARN)