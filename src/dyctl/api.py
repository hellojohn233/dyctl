"""JSON 工具：防御式取值 + 结构兼容。

抖音各接口的响应结构会随版本微调，所以取值一律走"缺字段不抛异常"的路径。
"""

from __future__ import annotations

from typing import Any


def dig(data: Any, *path: str | int, default: Any = None) -> Any:
    """按路径安全取值，路径不存在返回 default。"""
    cur = data
    for key in path:
        if isinstance(cur, dict):
            if key not in cur:
                return default
            cur = cur[key]
        elif isinstance(cur, list) and isinstance(key, int):
            if key >= len(cur):
                return default
            cur = cur[key]
        else:
            return default
    return cur if cur is not None else default


def pick(data: Any, *keys: str, default: Any = None) -> Any:
    """从 dict 中按顺序取第一个存在的 key。"""
    if not isinstance(data, dict):
        return default
    for k in keys:
        if k in data and data[k] is not None:
            return data[k]
    return default


def as_list(data: Any, *keys: str) -> list[dict]:
    """从响应中取出列表，兼容不同接口的包裹方式。"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in (*keys, "data", "aweme_list", "list", "items", "results", "comments"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict):
            nested = as_list(val, *keys)
            if nested:
                return nested
    return []
