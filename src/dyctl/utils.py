"""日志、输出与通用小工具。"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

console = Console()
err_console = Console(stderr=True)

_CONFIGURED = False


def setup_logging(verbose: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger().setLevel(logging.DEBUG if verbose else logging.INFO)
        return
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%H:%M:%S]",
        handlers=[RichHandler(console=err_console, rich_tracebacks=True, show_path=False)],
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ---- 人性化节奏：避免固定间隔的机械行为特征 ----


def jitter(lo: float = 0.35, hi: float = 1.1) -> float:
    return random.uniform(lo, hi)


def sleep_jitter(lo: float = 0.35, hi: float = 1.1) -> None:
    time.sleep(random.uniform(lo, hi))


def human_pause() -> None:
    """两次操作之间的随机停顿。"""
    time.sleep(random.uniform(0.4, 1.3))


# ---- 文本处理 ----


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value))
    return text.strip()


def human_size(num: Any) -> str:
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "-"
    for unit in ("", "w", "k", "m", "亿"):
        if abs(n) < 10_000:
            return f"{n:.0f}{unit}" if unit else f"{n:.0f}"
        n /= 10_000
    return f"{n:.1f}亿"


def ts_to_str(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def emit_json(payload: Any) -> None:
    """统一 JSON 输出，便于脚本消费。"""
    console.print_json(json.dumps(payload, ensure_ascii=False, default=str))


def make_table(title: str, columns: list[str]) -> Table:
    table = Table(title=title, header_style="bold cyan", title_style="bold")
    for col in columns:
        table.add_column(col, overflow="fold")
    return table
