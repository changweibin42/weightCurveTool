# -*- coding: utf-8 -*-
"""weightCurveTool 统一日志

日志带有 [INFO] / [OK] / [WARN] / [ERR] / [DBG] 标签。

级别（高 → 低过滤能力）：
    ERROR  仅错误
    WARN   + 警告（默认）
    OK     + 成功状态
    INFO   + 一般流程信息
    DEBUG  + 调试细节
    SILENT 全部静默

用法：
    # 包根（core.py / launcher.py）
    from .Logger import log

    # tools 子包
    from ..Logger import log

    log.info("进入边界模式")
    log.ok("已重建 3 条曲线")
    log.warn("无法确定 mesh")
    log.error("模型不存在")

全局开关：
    from weightCurveTool.Logger import log
    log.set_detailed(True)       # 打开详细日志
    log.set_detailed(False)      # 只留警告和错误
    log.set_level(log.SILENT)    # 静默
"""


DETAILED_LOGS = False


class _Logger(object):
    # ---- 级别 ----
    DEBUG  = 10
    INFO   = 20
    OK     = 25
    WARN   = 30
    ERROR  = 40
    SILENT = 100

    _LABELS = {
        DEBUG: "[DBG]",
        INFO:  "[INFO]",
        OK:    "[OK]",
        WARN:  "[WARN]",
        ERROR: "[ERR]",
    }

    def __init__(self, level=None):
        default = self.DEBUG if DETAILED_LOGS else self.WARN
        self._level = default if level is None else int(level)

    # ---- 全局开关 ----
    def set_level(self, level):
        self._level = int(level)

    def get_level(self):
        return self._level

    def set_detailed(self, enabled):
        self.set_level(self.DEBUG if enabled else self.WARN)

    def details_enabled(self):
        return self._level <= self.INFO

    # ---- 各级别入口 ----
    def debug(self, msg):
        self._write(self.DEBUG, msg)

    def info(self, msg):
        self._write(self.INFO, msg)

    def ok(self, msg):
        self._write(self.OK, msg)

    def warn(self, msg):
        self._write(self.WARN, msg)

    def error(self, msg):
        self._write(self.ERROR, msg)

    # ---- 内部 ----
    def _write(self, level, msg):
        if level < self._level:
            return
        label = self._LABELS.get(level)
        if label:
            print(f"{label} {msg}")
        else:
            print(str(msg))


log = _Logger()

_NATIVE_INFO_PREFIXES = (
    "[WCT Native Query]",
    "[WCT Native Strict]",
    "[WCT Native Project]",
)


def run_native_command(command, *args):
    """Hide this tool's native timing lines for one synchronous Maya command."""
    if log.details_enabled():
        return command(*args)

    from maya.api import OpenMaya as om

    active = True

    def filter_timing(message, message_type, _):
        return (
            active
            and message_type == om.MCommandMessage.kInfo
            and message.lstrip("#/ \t").startswith(_NATIVE_INFO_PREFIXES)
        )

    try:
        callback = om.MCommandMessage.addCommandOutputFilterCallback(filter_timing)
    except (AttributeError, RuntimeError):
        # Keep the original command available if this Maya version cannot filter.
        return command(*args)
    try:
        return command(*args)
    finally:
        active = False
        try:
            om.MMessage.removeCallback(callback)
        except RuntimeError:
            log.debug("无法移除已停用的原生日志过滤回调")
