"""Cookie 文件解析与注入。

支持从外部导入抖音登录态——这样就不必每次扫码，浏览器插件导出的
cookie 文件直接喂进来即可。

支持的格式（自动识别）：

1. **Netscape cookies.txt** —— 浏览器插件 "Get cookies.txt LOCALLY" /
   EditThisCookie / Cookie-Editor 的默认导出格式
2. **JSON 数组** —— Cookie-Editor / EditThisCookie 的 JSON 导出
3. **JSON 对象** —— 简单的 ``{"sessionid": "xxx"}``
4. **Playwright storage_state** —— ``{"cookies": [...], "origins": [...]}``

统一转成 Playwright 的 cookie 字典，由 Crawl4AI 的
``BrowserConfig(cookies=[...])`` → ``context.add_cookies()`` 注入。
因为用的是持久化 profile，注入的 cookie 会直接落进 profile 的 cookie 库，
**导入一次长期有效**。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .utils import get_logger

log = get_logger(__name__)

# 抖音相关域名后缀
DOUYIN_SUFFIXES = ("douyin.com", "douyinpic.com", "douyinstatic.com")

# Netscape cookies.txt 的分隔符（标准是 TAB，宽松兼容空格）
_FIELD_SEP = "\t"

_VALID_SAMESITE = {"Strict": "Strict", "Lax": "Lax", "None": "None"}


class CookieParseError(ValueError):
    """cookie 文件格式无法识别。"""


def _is_douyin(domain: str) -> bool:
    d = (domain or "").lstrip(".").lower()
    return any(d == s or d.endswith("." + s) for s in DOUYIN_SUFFIXES)


def _to_playwright(
    name: str,
    value: str,
    domain: str,
    path: str = "/",
    expires: Any = None,
    http_only: bool = False,
    secure: bool = False,
    same_site: str | None = None,
) -> dict:
    """转成 Playwright cookie 字典。"""
    # Playwright 用 expires=-1 表示会话 cookie
    if expires in (None, "", 0, "0"):
        exp = -1
    else:
        try:
            exp = int(float(expires))
        except (TypeError, ValueError):
            exp = -1
        if exp <= 0:
            exp = -1

    cookie: dict[str, Any] = {
        "name": name,
        "value": str(value),
        "domain": domain or ".douyin.com",
        "path": path or "/",
        "expires": exp,
        "httpOnly": bool(http_only),
        "secure": bool(secure),
    }
    if same_site in _VALID_SAMESITE:
        cookie["sameSite"] = same_site
    return cookie


def _parse_netscape(text: str) -> list[dict]:
    """Netscape cookies.txt 格式。"""
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") and "_HttpOnly_" not in line:
            # 注释行（但 #HttpOnly_ 前缀的是真数据）
            if not line.startswith("#HttpOnly_"):
                continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        parts = line.split(_FIELD_SEP)
        if len(parts) < 7:
            parts = line.split()
        if len(parts) < 7:
            continue
        domain, _sub, path, secure, expires, name, value = parts[:7]
        out.append(
            _to_playwright(
                name=name,
                value=value,
                domain=domain,
                path=path,
                expires=expires,
                secure=str(secure).upper() == "TRUE",
            )
        )
    return out


def _parse_json_list(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        same = item.get("sameSite")
        if isinstance(same, str):
            # Cookie-Editor 用 "no_restriction" 表示 None
            same = "None" if same.lower() in {"no_restriction", "none"} else same
            same = same.capitalize() if same.lower() in {"lax", "strict"} else same
        out.append(
            _to_playwright(
                name=name,
                value=item.get("value", ""),
                domain=item.get("domain", ".douyin.com"),
                path=item.get("path", "/"),
                expires=item.get("expirationDate", item.get("expires")),
                http_only=bool(item.get("httpOnly", False)),
                secure=bool(item.get("secure", False)),
                same_site=same,
            )
        )
    return out


def _parse_json_dict(data: dict) -> list[dict]:
    """简单 name -> value 映射，或 storage_state 结构。"""
    if "cookies" in data and isinstance(data["cookies"], list):
        return _parse_json_list(data["cookies"])
    out = []
    for name, value in data.items():
        if not isinstance(value, str | int | float):
            continue
        out.append(
            _to_playwright(
                name=name, value=str(value), domain=".douyin.com", path="/", expires=-1
            )
        )
    return out


def parse_cookie_file(path: str | Path) -> list[dict]:
    """解析 cookie 文件，返回 Playwright cookie 字典列表。"""
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"cookie 文件不存在: {p}")

    raw = p.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        raise CookieParseError(f"cookie 文件是空的: {p}")

    cookies: list[dict] = []

    # 优先按 JSON 解析
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CookieParseError(f"cookie 文件是 JSON 但格式有误: {exc}") from exc
        if isinstance(data, list):
            cookies = _parse_json_list(data)
        elif isinstance(data, dict):
            cookies = _parse_json_dict(data)
        else:
            raise CookieParseError("cookie JSON 顶层不是数组或对象")
    else:
        cookies = _parse_netscape(raw)

    if not cookies:
        raise CookieParseError(
            f"没从 {p.name} 里解析出任何 cookie。"
            "支持 Netscape cookies.txt / JSON 数组 / JSON 对象 / Playwright storage_state。"
        )
    return cookies


def filter_douyin(cookies: list[dict], only_douyin: bool = True) -> tuple[list[dict], int]:
    """只保留抖音域名的 cookie。返回 (保留的, 丢弃的)。"""
    if not only_douyin:
        return cookies, 0
    kept = [c for c in cookies if _is_douyin(c.get("domain", ""))]
    return kept, len(cookies) - len(kept)


def describe(cookies: list[dict]) -> str:
    """给用户看的简短描述。"""
    if not cookies:
        return "(空)"
    names = sorted({c["name"] for c in cookies})
    has_session = any(n in {"sessionid", "sessionid_ss", "sid_tt"} for n in names)
    head = f"{len(cookies)} 个 cookie / {len(names)} 个不同名字"
    if has_session:
        head += "（含登录态 cookie）"
    return f"{head}: {', '.join(names[:8])}{'…' if len(names) > 8 else ''}"


def export_cookies(ctx_cookies: list[dict], path: str | Path) -> Path:
    """把当前浏览器 cookie 导出成 Netscape cookies.txt。"""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Netscape HTTP Cookie File",
        "# 由 dyctl (dyctl) 导出",
        "",
    ]
    now = int(time.time())
    for c in ctx_cookies:
        domain = c.get("domain", "")
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path_attr = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = int(c.get("expires", 0) or 0)
        if expires <= 0:
            expires = now + 86400 * 30
        name = c.get("name", "")
        value = c.get("value", "")
        lines.append("\t".join([domain, include_sub, path_attr, secure, str(expires), name, value]))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
