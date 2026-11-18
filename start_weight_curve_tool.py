# -*- coding: utf-8 -*-
"""WeightCurveTool 启动文件。

把本文件放在 weightCurveTool 包内，或与 weightCurveTool 文件夹并排。
可以拖入 Maya 视口，也可以在 Script Editor 的 Python 页执行整份文件。
"""

import importlib
import os
from pathlib import Path
import sys

import maya.cmds as cmds


_PACKAGE = "weightCurveTool"
_PACKAGE_FILES = ("__init__.py", "launcher.py")


def _package_folder(location):
    """Accept either the package itself or its parent."""
    try:
        path = Path(location).expanduser().resolve()
        for candidate in (path, path / _PACKAGE):
            if candidate.name != _PACKAGE:
                continue
            if all((candidate / name).is_file() for name in _PACKAGE_FILES):
                return candidate
    except (OSError, TypeError, ValueError, RuntimeError):
        pass
    return None


def _source_folder():
    # Script Editor may retain a stale __file__ in its shared namespace.
    # co_filename belongs to this execution, including exec(compile(...)).
    filename = _source_folder.__code__.co_filename
    if not filename or filename.startswith("<"):
        return None
    path = Path(filename)
    return path.resolve().parent if path.is_file() else None


def _loaded_folder(module):
    filename = getattr(module, "__file__", None)
    return Path(filename).resolve().parent if filename else None


def _find_package():
    directory = _source_folder()
    if directory is not None:
        package = _package_folder(directory)
        if package is not None:
            return package

    loaded = sys.modules.get(_PACKAGE)
    if loaded is not None:
        package = _package_folder(_loaded_folder(loaded))
        if package is not None:
            return package

    maya_paths = os.environ.get("MAYA_SCRIPT_PATH", "").split(os.pathsep)
    for directory in (*sys.path, cmds.internalVar(userScriptDir=True), *maya_paths):
        if not directory:
            continue
        package = _package_folder(directory)
        if package is not None:
            return package

    selected = cmds.fileDialog2(
        caption="请选择 weightCurveTool 文件夹或它的上一级目录",
        fileMode=3,
        dialogStyle=2,
    )
    if not selected:
        return None
    package = _package_folder(selected[0])
    if package is None:
        raise RuntimeError("选中的目录没有找到完整的 weightCurveTool 包：{}".format(selected[0]))
    return package


def launch_weight_curve_tool():
    """Resolve the installed package and use its existing window entry point."""
    package = _find_package()
    if package is None:
        return None

    loaded = sys.modules.get(_PACKAGE)
    if loaded is not None:
        if _loaded_folder(loaded) != package:
            raise RuntimeError(
                "当前 Maya 已导入另一份 weightCurveTool（{}）。"
                "请重启 Maya 后再打开 {}。".format(_loaded_folder(loaded), package)
            )
        return loaded.show()

    parent = str(package.parent)
    original_path = sys.path[:]
    if parent in sys.path:
        sys.path.remove(parent)
    sys.path.insert(0, parent)
    try:
        tool = importlib.import_module(_PACKAGE)
        if _loaded_folder(tool) != package:
            raise RuntimeError("导入的 weightCurveTool 与选择的目录不一致：{}".format(_loaded_folder(tool)))
    except Exception:
        sys.path[:] = original_path
        raise
    return tool.show()


def onMayaDroppedPythonFile(*args, **kwargs):
    """Maya invokes this callback after the file is dropped in the viewport."""
    return launch_weight_curve_tool()


if __name__ == "__main__":
    launch_weight_curve_tool()
