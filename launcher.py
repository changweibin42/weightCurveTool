# launcher.py 完整
import os
from PySide2 import QtWidgets
import maya.OpenMayaUI as omui
import shiboken2

from .plugin_loader import load_all_plugins_in_folder
from .ui import WeightCurveEditorWindow, WINDOW_OBJ_NAME


_window = None


def load_all_plugins():
    """加载本包 plug-ins/ 下的所有 .mll 插件。

    ★ 薄包装：实际逻辑在 plugin_loader.load_all_plugins_in_folder，
      与外部工具共用同一实现，避免重复维护。
    """
    plugins_dir = os.path.join(os.path.dirname(__file__), "plug-ins")
    load_all_plugins_in_folder(plugins_dir)


def show():
    global _window

    # 1) 关掉自己持有的旧窗口（无论是否可见，都关 + 释放）
    if _window is not None:
        try:
            _window.close()
            _window.deleteLater()
        except Exception:
            pass
        _window = None

    # 2) 扫掉可能残留的同名窗口（脚本编辑器重跑时 Python 引用丢了，
    #    但 Qt 对象可能仍挂着）。按 objectName 匹配，与显示标题解耦。
    app = QtWidgets.QApplication.instance()
    if app:
        for widget in app.topLevelWidgets():
            if widget.objectName() == WINDOW_OBJ_NAME:
                try:
                    widget.close()
                    widget.deleteLater()
                except Exception:
                    pass

    # 3) 加载所有插件
    load_all_plugins()

    maya_main = shiboken2.wrapInstance(int(omui.MQtUtil.mainWindow()), QtWidgets.QWidget)
    _window = WeightCurveEditorWindow(parent=maya_main)
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window