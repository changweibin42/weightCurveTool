# -*- coding: utf-8 -*-
"""UI 控件 —— hover 按钮、贝塞尔曲线控件、参数组、浮点滑动条"""
try:
    from PySide2.QtWidgets import *
    from PySide2.QtCore import *
    from PySide2.QtGui import *
except ImportError:
    from PySide.QtGui import *
    from PySide.QtCore import *


# ============================================================================
# hover 按钮
# ============================================================================

class HoverButton(QPushButton):
    def __init__(self, text, hover_text="", parent=None):
        super().__init__(text, parent)
        self.hover_text = hover_text
        self._on_hover = None
        self._on_leave = None

    def set_hover_callbacks(self, on_hover, on_leave):
        self._on_hover = on_hover
        self._on_leave = on_leave

    def update_hover_text(self, hover_text):
        self.hover_text = hover_text

    def enterEvent(self, event):
        if self._on_hover:
            try:
                self._on_hover(self.hover_text)
            except Exception:
                pass
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._on_leave:
            try:
                self._on_leave()
            except Exception:
                pass
        super().leaveEvent(event)


# ============================================================================
# 贝塞尔曲线控件
# ============================================================================

class WeightCurveWidget(QWidget):
    valueChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = [
            [0.0, 0.0],
            [0.5, 0.0],
            [0.5, 1.0],
            [1.0, 1.0]
        ]
        self.mirror_enabled = False
        self.__movePoint = -1
        self.__dragThreshold = 10
        self.setFixedSize(512, 448)
        self.setFocusPolicy(Qt.StrongFocus)

    # ─── 镜像 API ───────────────────────────────────────────
    def set_mirror(self, enabled):
        """启用/关闭中心对称镜像。

        开启时如果 p1/p2 当前不对称，立刻强制 p2 = (1-x1, 1-y1)。
        """
        self.mirror_enabled = bool(enabled)
        if self.mirror_enabled:
            x1, y1 = self.points[1]
            self.points[2] = [1.0 - x1, 1.0 - y1]
            self.update()
            self.valueChanged.emit()
        else:
            self.update()

    def apply_mirror_to_x(self):
        """仅对 X 强制镜像：p2.x = 1 - p1.x（保持 Y 不动）。"""
        if not self.mirror_enabled:
            return
        x1 = self.points[1][0]
        self.points[2][0] = 1.0 - x1

    def apply_mirror_to_y(self):
        """仅对 Y 强制镜像：p2.y = 1 - p1.y（保持 X 不动）。"""
        if not self.mirror_enabled:
            return
        y1 = self.points[1][1]
        self.points[2][1] = 1.0 - y1

    # ─── 绘制 ────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setBrush(QBrush(QColor(60, 60, 60), Qt.SolidPattern))
        painter.setPen(QPen(QColor(0, 0, 0), 1, Qt.SolidLine))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

        def to_pixel(px, py):
            return QPointF(px * (self.width() - 1),
                           (1 - py) * (self.height() - 1))

        p0 = to_pixel(*self.points[0])
        p1 = to_pixel(*self.points[1])
        p2 = to_pixel(*self.points[2])
        p3 = to_pixel(*self.points[3])

        path = QPainterPath()
        path.moveTo(p0)
        path.cubicTo(p1, p2, p3)
        painter.setPen(QPen(QColor(0, 255, 0), 2.5))
        painter.drawPath(path)

        painter.setPen(QPen(QColor(150, 150, 150), 1, Qt.DotLine))
        w_step = (self.width() - 1) / 10.0
        h_step = (self.height() - 1) / 10.0
        for i in range(1, 10):
            w = w_step * i
            h = h_step * i
            painter.drawLine(w, 0, w, self.height())
            painter.drawLine(0, h, self.width(), h)

        painter.setPen(QPen(QColor(0, 0, 0), 1, Qt.SolidLine))
        painter.setBrush(QBrush(QColor(255, 200, 100), Qt.SolidPattern))
        painter.drawEllipse(p1, 6, 6)
        painter.drawEllipse(p2, 6, 6)

        painter.setBrush(QBrush(QColor(150, 150, 150)))
        painter.drawEllipse(p0, 4, 4)
        painter.drawEllipse(p3, 4, 4)

        painter.setPen(QPen(QColor(180, 180, 180), 1, Qt.DashLine))
        painter.drawLine(p0, p1)
        painter.drawLine(p3, p2)

        painter.setPen(QPen(QColor(100, 100, 100), 1, Qt.SolidLine))
        edge_points = []
        for w, h in zip([0, 0, 1, 1, 0], [0, 1, 1, 0, 0]):
            p = QPointF(w * (self.width() - 1), h * (self.height() - 1))
            edge_points.extend([p, p])
        painter.drawLines(edge_points[1:-1])

        painter.setPen(QColor(255, 255, 0))
        font = painter.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(5, self.height() - 5, "(0,0)")
        painter.drawText(self.width() - 45, 18, "(1,1)")
        painter.drawText(self.width() - 20, self.height() - 5, "X")
        painter.drawText(5, 18, "Y")

        # 镜像提示
        if self.mirror_enabled:
            painter.setPen(QColor(100, 200, 255))
            font2 = painter.font()
            font2.setPointSize(10)
            font2.setBold(True)
            painter.setFont(font2)
            painter.drawText(10, 28, "镜像：ON")

        painter.end()

    # ─── 鼠标事件 ──────────────────────────────────────────
    def mousePressEvent(self, event):
        self.setFocus()
        pos = event.pos()
        for idx in [1, 2]:
            px = self.points[idx][0] * (self.width() - 1)
            py = (1 - self.points[idx][1]) * (self.height() - 1)
            dx = pos.x() - px
            dy = pos.y() - py
            if (dx * dx + dy * dy) < (self.__dragThreshold
                                       * self.__dragThreshold):
                self.__movePoint = idx
                return
        self.__movePoint = -1

    def mouseMoveEvent(self, event):
        if self.__movePoint in [1, 2]:
            pos = event.pos()
            x = max(0.0, min(1.0, pos.x() / (self.width() - 1)))
            y = max(0.0, min(1.0, 1.0 - pos.y() / (self.height() - 1)))
            self.points[self.__movePoint] = [x, y]

            if self.mirror_enabled:
                other = 3 - self.__movePoint  # 1↔2
                self.points[other] = [1.0 - x, 1.0 - y]

            self.update()
            self.valueChanged.emit()

    def mouseReleaseEvent(self, event):
        self.__movePoint = -1

    def get_curve_data(self):
        return self.points

    def set_curve_data(self, p0, p1, p2, p3):
        self.points = [p0, p1, p2, p3]
        self.update()
        self.valueChanged.emit()


# ============================================================================
# 浮点滑动条（4 输入控件组 → spin + slider 组合）
# ============================================================================

class FloatSliderGroup(QHBoxLayout):
    valueChange = Signal(float)

    def __init__(self):
        QHBoxLayout.__init__(self)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(3)
        self.spin.setSingleStep(0.01)
        self.slider = QSlider(Qt.Horizontal)
        self.spin.setStyleSheet(
            "background-color: #4a4a4a; color: white; font-size: 11pt;")
        self.addWidget(self.spin)
        self.addWidget(self.slider)
        self._min = 0.0
        self._max = 1.0
        self.set_range(0.0, 1.0)
        self.spin.valueChanged.connect(self._on_spin_changed)
        self.slider.valueChanged.connect(self._on_slider_changed)

    def _on_spin_changed(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(value * 1000)))
        self.slider.blockSignals(False)
        self.valueChange.emit(float(value))

    def _on_slider_changed(self, value):
        v = value / 1000.0
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self.valueChange.emit(float(v))

    def set_range(self, min_value, max_value):
        self._min = float(min_value)
        self._max = float(max_value)
        self.spin.setRange(self._min, self._max)
        self.slider.setRange(int(round(self._min * 1000)),
                             int(round(self._max * 1000)))

    def value(self):
        return self.spin.value()

    def set_value(self, value):
        self.spin.setValue(float(value))


# ============================================================================
# 参数组（4 输入框 + 按钮）
# ============================================================================

class WeightParamGroupMulti(QHBoxLayout):
    def __init__(self, label_text, button_text="应用", hover_text=""):
        super().__init__()
        self.label = QLabel(label_text)
        self.label.setFixedWidth(90)
        self.label.setStyleSheet(
            "color: #FFD700; font-size: 12pt; font-weight: bold;")
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.addWidget(self.label)

        self.line_edits = []
        for i in range(4):
            le = QLineEdit()
            le.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            le.setStyleSheet(
                "background-color: #4a4a4a; color: white; "
                "font-size: 11pt; border: 1px solid #777;")
            self.addWidget(le)
            self.line_edits.append(le)

        self.btn = HoverButton(button_text, hover_text)
        self.btn.setFixedWidth(60)
        self.btn.setStyleSheet(
            "background-color: #5a5a5a; color: white; font-size: 11pt;")
        self.addWidget(self.btn)

    def get_values(self):
        vals = []
        for le in self.line_edits:
            try:
                v = float(le.text())
                v = max(0.0, min(1.0, v))
                vals.append(v)
            except ValueError:
                return None
        return vals

    def set_values(self, values):
        if values and len(values) == 4:
            for i, v in enumerate(values):
                self.line_edits[i].setText(f"{v:.3f}")

    def on_clicked(self, callback):
        self.btn.clicked.connect(callback)

    def set_hover_callbacks(self, on_hover, on_leave):
        self.btn.set_hover_callbacks(on_hover, on_leave)


# ============================================================================
# 参数组（1 输入框 + 按钮）
# ============================================================================

class WeightParamGroup(QHBoxLayout):
    def __init__(self, label_text, default_text="", button_text="应用",
                 hover_text=""):
        super().__init__()
        self.label = QLabel(label_text)
        self.label.setFixedWidth(80)
        self.label.setStyleSheet(
            "color: white; font-size: 12pt; font-weight: bold;")
        self.line_edit = QLineEdit(default_text)
        self.line_edit.setStyleSheet(
            "background-color: #4a4a4a; color: white; "
            "font-size: 11pt; border: 1px solid #777;")
        self.btn = HoverButton(button_text, hover_text)
        self.btn.setStyleSheet(
            "background-color: #5a5a5a; color: white; font-size: 11pt;")
        self.addWidget(self.label)
        self.addWidget(self.line_edit)
        self.addWidget(self.btn)

    def get_text(self):
        return self.line_edit.text()

    def set_text(self, text):
        self.line_edit.setText(text)

    def on_clicked(self, callback):
        self.btn.clicked.connect(callback)

    def set_hover_callbacks(self, on_hover, on_leave):
        self.btn.set_hover_callbacks(on_hover, on_leave)