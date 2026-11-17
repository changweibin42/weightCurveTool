# -*- coding: utf-8 -*-
"""plugin_loader —— 扫描并加载指定文件夹下的所有 .mll 插件"""
import os
import maya.cmds as cmds

from .Logger import log


def load_all_plugins_in_folder(folder_path):
    """扫描指定文件夹，加载所有 .mll 插件。

    参数：
        folder_path  绝对路径
    返回：
        加载成功的插件名列表
    """
    if not os.path.isdir(folder_path):
        log.warn(f"插件文件夹不存在: {folder_path}")
        return []

    loaded = []
    for file in os.listdir(folder_path):
        if not file.endswith(".mll"):
            continue
        plugin_path = os.path.join(folder_path, file)
        plugin_name = os.path.splitext(file)[0]   # 去掉 .mll 扩展名

        # 检查是否已加载
        if cmds.pluginInfo(plugin_name, query=True, loaded=True):
            log.info(f"插件 '{plugin_name}' 已加载，跳过")
            continue

        # 尝试加载
        try:
            cmds.loadPlugin(plugin_path)
            log.ok(f"插件 '{plugin_name}' 加载成功")
            loaded.append(plugin_name)
        except Exception as e:
            log.error(f"加载插件 '{plugin_name}' 失败: {e}")

    return loaded