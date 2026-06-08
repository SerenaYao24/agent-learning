#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
题材行解析/格式化公共工具

题材行格式： # XXX（YYY）
- XXX = 题材名称（用于分类/匹配）
- YYY = 题材备注（可频繁修改，不影响分类）
"""

import re


def parse_topic_header(line: str) -> tuple:
    """
    解析题材标题行，拆分 topic_name 和 topic_note。

    参数:
        line: 原始行，如 '# 电阻电容（其他）' 或 '# 液冷' 或 '## 光通信'

    返回:
        (topic_name, topic_note)
        - topic_name: 纯题材名，用于分类/匹配，如 '电阻电容'
        - topic_note: 备注，如 '其他'；无备注时为空字符串 ''
    """
    # 去掉前导 # 和空白
    content = line.lstrip('#').strip()

    if not content:
        return content, ''

    # 尝试匹配 XXX（YYY）或 XXX(YYY)
    m = re.match(r'^(.+?)[（(](.+)[）)]$', content)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    return content, ''


def parse_topic_header_from_line(line: str, prefix: str = '# ') -> tuple:
    """
    从已知前缀行解析题材（兼容 ## / # / 无前缀等情况）。

    参数:
        line: 原始行
        prefix: 已知前缀，如 '# ' 或 '## ' 等

    返回:
        (topic_name, topic_note)
    """
    content = line[len(prefix):].strip()
    m = re.match(r'^(.+?)[（(](.+)[）)]$', content)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return content, ''


def format_topic_header(topic_name: str, topic_note: str = '') -> str:
    """
    将 topic_name + topic_note 还原为完整的题材标题行。

    参数:
        topic_name: 题材名
        topic_note: 备注（可选）

    返回:
        如 '# 电阻电容（其他）' 或 '# 液冷'
    """
    if topic_note:
        return f'# {topic_name}（{topic_note}）'
    return f'# {topic_name}'
