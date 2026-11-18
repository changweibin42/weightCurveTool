weightCurveTool V17.9.2 — 交点查询复用实验版

基于已整合 GitHub d1bee9e 的 V17.9.1，插件版本 1.8.2。
节点 ID 仍为 0x00142A04，没有新增节点 ID。
完整源码随包；不包含预编译 .mll / .pyd。

一、本次实现
针对日志中衰减交点批处理约 0.225～0.345 秒，新增三个可选优化：
1. 最近下界优先的候选面搜索。
   先按包围盒距离下界排序，只在上一面也属于最近候选时优先查询上一面。
   保留原来的浮点误差保护、距离比较和歧义回退；继续调用 Maya 求最终点。
2. 同一批次的精确最近点结果缓存。
   查询坐标按三个 double 的原始位模式作为键；不舍入、不合并相近点。
   只缓存成功返回的坐标和原始面 ID；一个命令结束即释放，最多 16384 项。
3. 同一批次的拓扑邻域缓存。
   expand({a,b},r) = expand({a},r) ∪ expand({b},r)。
   复用单顶点 BFS 结果，通过有序集合并集得到原来完全相同的邻域。
   最多保存 1024 个邻域，且保存的邻域顶点总数不超过 32768。

只改变衰减交点命令中的搜索和重复计算。
曲线最近点、交点探测数量、二分次数、容差、分区规则、线性求解、贝塞尔衰减均保留。
严格范围法线查询和 V17.9.1 批量贴面命令不启用本轮优化。
旧 BVH 遍历保留，复用未启用时使用原八参数命令接口。

相对 V17.9.1，运行代码只涉及：
- tools/NativeEdgeCuts.py：可选第九参数、缓存模式指纹。
- tools/NativeQueryReuse.py：新增二进制/查询配置绑定的启用记录。
保留你的 RestPoseSurface 缓存校验、共享 Hermite、代码清理和拖动交互。

二、编译安装
关闭 Maya，将包内 wct_surface_curve_native 内容合并到：
D:\wct_surface_curve_native
在 VS2022 x64 Developer Command Prompt 中逐行执行：

cd /d D:\wct_surface_curve_native
set "MAYA_DEVKIT=C:\Users\changweibin\devkitBase"
build_maya2024.cmd
install_maya2024.cmd

安装脚本需要本次 1.8.2 编译成功标记，复制新插件和上述两个 Python 文件。
默认工具目录 D:\maya2024\scripts\weightCurveTool。
保留现有 .pyd，不覆盖 RestPoseSurface.py，不需要修改启动脚本。
本安装脚本是给已安装 V17.9.1 的工具升级使用。
备份后缀：.pre_1_8_2.mll.bak / .pre_17_9_2.bak。

三、重启并验证
插件文件改变，旧验证记录会失效。重启 Maya，打开工具，激活有效的三条曲线组，
进入“修改曲线”，然后按顺序运行以下三个脚本。
每次只替换 path 的文件名，使用相同的执行方式：

path = r"D:\wct_surface_curve_native\python\maya_audit_strict_boundary_queries.py"
with open(path, encoding="utf-8") as f:
    exec(compile(f.read(), path, "exec"))

第二个 path：
D:\wct_surface_curve_native\python\maya_audit_native_projection.py

第三个 path：
D:\wct_surface_curve_native\python\maya_audit_query_reuse.py

第三个脚本执行：
- 两种路径预热后，交替顺序各测五轮完整交点批处理，比较中位耗时。
- 每轮逐项比较完整交点列表和端点数值，要求完全一致。
- 分别检查 main / fwd / bwd 的局部重算 mask。
- 检查边约束、固定值、衰减场、衰减坐标、两侧标签、脊线标签完全一致。
- 新路径至少快 3% 才保存启用记录，否则保持原路径。
- 检查选定路径的冷/热缓存结果，成功后保留已验证的热缓存。
- 报错会清除本轮启用记录和内存缓存；临时环境变量始终恢复。
这些审计不写权重、不修改曲线或选择；通过时只保存运行偏好。

通过后会看到：
[WCT Query Reuse Audit] SELECTED reuse=1 ...
记录绑定新插件 SHA256 和当前四个既有查询开关。换插件或切换查询配置后，记录失效。
验证范围是当前模型/曲线组，新模型及特殊折叠区域仍需检查。
未审计、验证不一致或没有明确速度收益时，新优化保持关闭。

四、正常拖动查看
分别拖动 main / fwd / bwd，查看：
[WCT Native Query] ... query_reuse=1 point_memo_hits=... ring_memo_hits=...
[WCT Native Cuts] batch=...
[WCT 松手耗时] ... 总计=...

point_memo_hits 为完全相同坐标的查询命中数，ring_memo_hits 为邻域命中数。
face_queries 为真正调用 Maya 单面最近点的次数。
face_fallbacks 仍会出现；模糊的查询继续使用完整网格，不能直接删掉。
真正收益取决于这些重复查询比例和模型形状，不保证每个案例都更快。
若审计输出 Original path retained，表示当前案例收益不足，继续原来的路径即可。

临时停用新优化（保留既有原生交点、严格范围和批量贴面加速）：
import os
os.environ['WCT_NATIVE_QUERY_REUSE'] = '0'

恢复自动根据验证记录选择：
os.environ.pop('WCT_NATIVE_QUERY_REUSE', None)

清除本轮记录：
from weightCurveTool.tools import NativeQueryReuse as Q
Q.clear_audit()

五、本地验证
完整结果见 verification.json。
开发验证环境需 numpy/scipy/g++：
python -X utf8 -m unittest discover -s tests -q
python -X utf8 -m unittest discover -s verification/curves/tests -q

包含旧遍历源码保护、10000 次新旧候选查询对比、1800 组 BFS/邻域缓存对比、
有符号零/一浮点单位/非有限数缓存检查、缓存边界及失败结果不缓存、
旧八参数/新九参数命令解析、局部 mask、审计不一致拒绝、速度不足拒绝、状态恢复，
以及原有严格范围和衰减回归。
C++ 实现用 g++ 和最小 SDK 替身检查，不能代替真实 Maya SDK/ABI 和场景验证。
本环境没有执行 Windows MSVC 编译，也未实测新优化在 Maya 中的收益。

回退：Maya 关闭时恢复 .pre_1_8_2.mll.bak 和 NativeEdgeCuts.py.pre_17_9_2.bak。
