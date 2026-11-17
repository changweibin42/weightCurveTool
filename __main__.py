# -*- coding: utf-8 -*-
"""weightCurveTool 开发启动入口

用法（三选一）：
    1. 脚本管理器里点 weightCurveTool 分页的按钮（自动走 runpy.run_module）
    2. Maya 脚本编辑器：
           import runpy
           runpy.run_module("weightCurveTool", run_name="__main__", alter_sys=True)
    3. 命令行：python -m weightCurveTool（父目录在 PYTHONPATH 时）

注意：本文件用于开发阶段"热重载 + 打开窗口"。生产环境直接：
    import weightCurveTool; weightCurveTool.show()
"""

from __future__ import print_function

import importlib
import sys


# 依赖顺序：底层 → 顶层，包本身放最后（让 __init__.py 重新绑定 show）
_RELOAD_ORDER = (
    'weightCurveTool.tools',
    'weightCurveTool.tools.MayaSelectors',
    'weightCurveTool.tools.ColorScheme',
    'weightCurveTool.tools.CurveRefine',
    'weightCurveTool.tools.curve_core',
    'weightCurveTool.tools.linear_regression',
    'weightCurveTool.tools.kd_tree_copy',
    'weightCurveTool.tools.geodesic',
    'weightCurveTool.tools.mds',
    'weightCurveTool.tools.RestPoseSurface',
    'weightCurveTool.tools.CurveLocalPoints',

    'weightCurveTool.tools.GroupManager',
    'weightCurveTool.tools.JointState',
    'weightCurveTool.tools.SurfaceBinder',
    'weightCurveTool.tools.VertexColorDisplay',
    'weightCurveTool.tools.SurfaceRegion',
    'weightCurveTool.tools.NativeEdgeCuts',
    'weightCurveTool.tools.StrictCurveRegion',
    'weightCurveTool.tools.BoundaryWriteHistory',
    'weightCurveTool.tools.LegacyBoundaryAlgorithms',
    'weightCurveTool.tools.WeightCalculator',
    'weightCurveTool.tools.SkinWeightPainter',
    'weightCurveTool.tools.WeightColorPipeline',
    'weightCurveTool.tools.ProjectionProfiles',
    'weightCurveTool.tools.ProjectionRegions',
    'weightCurveTool.tools.LegacyWeightPainter',   # ★ 新增
    'weightCurveTool.tools.JointSurfaceBuilder',
    'weightCurveTool.tools.BoundaryCorrespondence',
    'weightCurveTool.tools.DualCurveBuilder',
    'weightCurveTool.tools.SurfaceSlideBuilder',

    'weightCurveTool.tools.NativeCurvePreview',
    'weightCurveTool.tools.CurveBuildReuse',
    'weightCurveTool.tools.ModifyCurveTool',
    'weightCurveTool.tools.BoundaryFlow',
    'weightCurveTool.tools.CreateCurveFlow',

    'weightCurveTool.plugin_loader',
    'weightCurveTool.core',
    'weightCurveTool.ui_widgets',
    'weightCurveTool.ui',
    'weightCurveTool.launcher',

    'weightCurveTool',   # 包本身放最后
)


def _close_existing_window():
    try:
        import weightCurveTool.launcher as _launcher
        win = getattr(_launcher, '_window', None)
        if win is not None:
            try:
                win.close()
                win.deleteLater()
            except Exception:
                pass
            _launcher._window = None
    except Exception:
        pass


def _reset_core_context():
    try:
        import weightCurveTool.core as _core
        _core.reset_context()
    except Exception as e:
        print("[weightCurveTool] 重置 core 上下文失败: {}".format(e))


def _ensure_logger():
    name = 'weightCurveTool.Logger'
    if name in sys.modules:
        importlib.reload(sys.modules[name])
    else:
        importlib.import_module(name)
    from weightCurveTool.Logger import log
    return log


def reload_all(log=None):
    for name in _RELOAD_ORDER:
        if name in sys.modules:
            if log:
                log.info("reload: {}".format(name))
            try:
                importlib.reload(sys.modules[name])
            except Exception as e:
                if log:
                    log.error("reload 失败: {}: {}".format(name, e))
                else:
                    print("[weightCurveTool] reload 失败: {}: {}".format(name, e))
        else:
            if log:
                log.info("import: {}".format(name))
            try:
                importlib.import_module(name)
            except Exception as e:
                if log:
                    log.error("import 失败: {}: {}".format(name, e))
                else:
                    print("[weightCurveTool] import 失败: {}: {}".format(name, e))


def reload_and_show():
    """关闭旧窗口 → 重置上下文 → 全量 reload → 打开新窗口。"""
    _close_existing_window()
    _reset_core_context()

    log = _ensure_logger()
    reload_all(log)

    import weightCurveTool
    return weightCurveTool.show()


if __name__ == "__main__":
    reload_and_show()