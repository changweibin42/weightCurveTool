# WeightCurveTool

Maya 蒙皮权重工具，包含曲线边界和骨骼投影两种工作方式。仓库提供 Windows x64、Maya 2024 对应的原生插件和 Python 扩展，以及两者的构建源码。

## 在另一台电脑运行

在 Git Bash 里进入要存放工具的目录，然后执行以下命令。保留克隆目录名 `weightCurveTool`，启动脚本会按这个包名查找工具。

```bash
git lfs install
git clone https://github.com/changweibin42/weightCurveTool.git weightCurveTool
cd weightCurveTool
git lfs pull
git lfs ls-files
```

`git lfs ls-files` 输出中，以下两项应带有 `*`，表示工作目录里已有完整二进制。使用 Maya 2024 的 Python 环境确认能导入 NumPy 和 SciPy。

- `plug-ins/wctSurfaceCurve.mll`
- `tools/_wct_profiles_native.cp310-win_amd64.pyd`

### 启动

- **拖动启动**：把仓库根目录的 `start_weight_curve_tool.py` 拖入 Maya 视口。
- **Script Editor 启动**：在 Maya Script Editor 的 **Python** 页打开同一个文件，执行整份脚本。如果 Maya 弹出目录选择框，选择 `weightCurveTool` 文件夹或其上一级目录。

### 详细日志

默认只在 Script Editor 中输出警告和错误。排查问题时，可在 Maya 的 Python 页执行以下代码，立即打开详细日志。

```python
from weightCurveTool.Logger import log
log.set_detailed(True)
```

排查完执行 `log.set_detailed(False)`。如需每次启动都打开详细日志，把 `Logger.py` 中的 `DETAILED_LOGS` 改为 `True`，然后重启 Maya。

## 仓库目录

| 路径 | 内容 |
| --- | --- |
| 根目录和 `tools/` | Python 启动入口、界面和权重处理 |
| `plug-ins/` | Maya 插件，`.mll` 由 Git LFS 管理 |
| `native/wct_profiles_native/` | Cython 权重曲线扩展源码、编译和验证 |
| `native/wct_surface_curve_native/` | Maya C++ 插件源码、编译和安装 |
| `native/wct_surface_curve_native/python/` | 在 Maya 内运行的测试与性能审计 |
| `native/wct_surface_curve_native/tests/` | 开发回归测试及对照源码 |

[原生组件编译与验证](docs/native-build.md)
