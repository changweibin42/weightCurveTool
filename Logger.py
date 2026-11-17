# -*- coding: utf-8 -*-
"""weightCurveTool 统一日志

★ 只输出 ASCII 标签（[INFO] / [OK] / [WARN] / [ERR] / [DBG]），
  在任何 stdout 编码（UTF-8 / GBK / 重定向文件）下都不会抛 UnicodeEncodeError。

级别（高 → 低过滤能力）：
    ERROR  仅错误
    WARN   + 警告
    OK     + 成功状态
    INFO   + 一般流程信息（默认）
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
    log.set_level(log.DEBUG)     # 打开调试
    log.set_level(log.WARN)      # 只留警告和错误
    log.set_level(log.SILENT)    # 静默
"""


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
        self._level = self.INFO if level is None else int(level)

    # ---- 全局开关 ----
    def set_level(self, level):
        self._level = int(level)

    def get_level(self):
        return self._level

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