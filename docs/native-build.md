# 原生组件编译与验证

常规安装使用仓库中的 Git LFS 二进制。需要修改原生组件或重新生成二进制时，使用下列流程。

## C++ 插件

在 **VS 2022 x64 Developer Command Prompt** 中，从仓库根目录执行这些命令。将 `MAYA_DEVKIT` 改为 Maya 2024 devkit 的实际位置，Maya 安装路径也可以通过 `MAYA_LOCATION` 指定。

```bat
cd native\wct_surface_curve_native
set "MAYA_DEVKIT=C:\path\to\maya2024\devkitBase"
build_maya2024.cmd
```

构建产物是 `dist/wctSurfaceCurve.mll`。首次验证可在 Maya 的 Python Script Editor 中执行本目录的 `python/maya_smoke_test.py`，它会创建测试节点。关闭 Maya 后执行 `install_maya2024.cmd`，脚本默认安装到当前仓库的 `plug-ins/`。需要安装到另一个现有的工具目录时，在同一个命令行先设置 `WCT_INSTALL_ROOT`。

## Cython 扩展

在 **VS 2022 x64 Developer Command Prompt** 中，从仓库根目录执行。将 `WCT_PYTHON_DEV` 改为本机含有 `include/Python.h` 和 `libs/python310.lib` 的 64 位 Python 3.10 目录。一并确认 Maya 2024 的 `mayapy` 已安装 Cython、setuptools 和 wheel。

```bat
cd native\wct_profiles_native
set "WCT_PYTHON_DEV=C:\path\to\Python310"
build_maya2024.cmd
```

脚本会编译扩展，然后运行正确性验证与基准测试。关闭 Maya，将生成在 `weightCurveTool/tools/` 下的 `_wct_profiles_native.cp310-win_amd64.pyd` 复制到仓库根目录的 `tools/`。

## 验证

在仓库根目录执行开发回归测试。此环境需要 NumPy、SciPy 和可在命令行调用的 `g++`。

```bash
python -X utf8 -m unittest discover -s native/wct_surface_curve_native/tests -q
```

在 Maya 内验证新插件时，可从 `native/wct_surface_curve_native/python/` 运行对应的审计脚本。先打开工具并激活有效的三条曲线，再依次执行 `maya_audit_strict_boundary_queries.py`、`maya_audit_native_projection.py` 和 `maya_audit_query_reuse.py`。交点查询复用根据审计结果启用。设置 `WCT_NATIVE_QUERY_REUSE=0` 可关闭这项实验。

开发回归测试使用合成场景和 Maya API 替身。Windows 插件加载与实际场景表现需要在 Maya 里验证。
