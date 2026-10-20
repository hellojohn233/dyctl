"""Crawl4AI 引擎：抖音网页端自动化的统一底座。

为什么全栈用 Crawl4AI 而不是自己管 Playwright：
    Crawl4AI 本身就建立在 Playwright 之上，但白送了一堆东西——持久化 profile、
    stealth、浏览器池、Markdown 抽取、批量并发调度、代理轮换、
    以及 Docker server / MCP 的部署形态。自己重写这些没有意义。

本模块的三条设计要点：

1. **动作层用钩子。** Crawl4AI 的 `arun` 生命周期是「导航 → 抽取 → 返回」，
   而抖音的写操作（上传、填简介、点发布）需要页面级动作。
   解决办法是 `on_execution_started` 钩子——它交给你的就是**原始 Playwright page**。

   ⚠️ 坑：该钩子嵌套在 `if config.js_code:` 内部（async_crawler_strategy.py:1050），
   不设 `js_code` 它根本不会触发。本模块统一注入一个无害的 js_code 来兜底。

2. **数据层用 expect_response。** 抖音接口带 `a_bogus` 签名，自己构造请求必失败。
   但页面自己发的请求一定合法——所以让页面去发，我们用 `expect_response`
   精确等待并解析 JSON 响应体。比事后在几千条事件里翻要快也准。

3. **指纹层自己兜底。** Crawl4AI 的 BrowserConfig 默认 UA 写死为
   Linux Chrome 116，而 `sec-ch-ua` 由它推导。在 macOS 上跑真实 Chrome 155
   却发 Linux UA，UA / 系统 / Client Hints 三方矛盾。这里按本机真实版本重建 UA。
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .selectors import BLOCK
from .utils import get_logger

log = get_logger(__name__)

# 抖音网页端入口
URLS = {
    "home": "https://www.douyin.com/",
    "creator_home": "https://creator.douyin.com/creator-micro/home",
    "upload": "https://creator.douyin.com/creator-micro/content/upload",
    "content_manage": "https://creator.douyin.com/creator-micro/content/manage",
    "data_center": "https://creator.douyin.com/creator-micro/data-center/content",
    "login": "https://www.douyin.com/",
}

# 代表「已登录」的 cookie。注意 passport_csrf_token 游客也会有，不能用来判断。
LOGIN_COOKIES = {"sessionid", "sessionid_ss", "sid_tt"}

# 关键接口路径
API = {
    "post_list": "/aweme/v1/web/aweme/post/",
    "comment_list": "/aweme/v1/web/comment/list/",
    "comment_reply": "/aweme/v1/web/comment/reply/",
    "data_overview": "/aweme/v1/web/databoard/",
    "search_single": "/aweme/v1/web/general/search/single/",
    "search_item": "/aweme/v1/web/search/item/",
    "user_profile": "/aweme/v1/web/user/profile/other/",
    "user_post": "/aweme/v1/web/aweme/post/",
    "hot_search": "/aweme/v1/web/hot/search/list/",
    "query_user": "/aweme/v1/web/query/user/",
    "social_count": "/aweme/v1/web/social/count",
    "user_risk": "/aweme/v1/web/get/user/risklevel/",
    "user_settings": "/aweme/v1/web/get/user/settings/",
    "resource_list": "/aweme/v1/web/solution/resource/list/",
    "notification": "/aweme/v1/web/external/notification/",
}

# 本机可能安装的浏览器（用于取真实版本号）
BROWSER_BINARIES = [
    ("Google Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("Microsoft Edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ("Chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium"),
]

MAC_UA_TEMPLATE = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{version} Safari/537.36"
)

# 无害的 js_code：唯一作用是让 on_execution_started 钩子被触发
_HOOK_TRIGGER_JS = "void 0;"


def native_chrome_ua() -> str | None:
    """构造与本机真实浏览器一致的 UA 字符串。

    Crawl4AI 默认 UA 写死为 Linux Chrome 116，且 `sec-ch-ua` 由它推导。
    在 macOS 上跑真实 Chrome 155 却发 Linux UA，UA / 操作系统 / Client Hints
    三方互相矛盾——这类自相矛盾的特征比无头模式更隐蔽，也更容易被风控抓到。

    这里读取本机浏览器真实版本号重建 UA，Client Hints 会跟着变成同一版本。
    找不到可识别的浏览器时返回 None，交给浏览器用原生 UA。
    """
    for name, path in BROWSER_BINARIES:
        if not Path(path).exists():
            continue
        try:
            out = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=10
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("读取 %s 版本失败: %s", name, exc)
            continue
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", (out.stdout or "") + (out.stderr or ""))
        if m:
            ua = MAC_UA_TEMPLATE.format(version=m.group(1))
            log.debug("本机浏览器 %s，UA 设为 %s", name, ua)
            return ua
    return None


# ---------------------------------------------------------------- 风控识别

BLOCK_SELECTORS = BLOCK


async def detect_block(page) -> str | None:
    """检测页面是否被抖音风控拦截。返回 None 表示正常。

    实测（2026-10）：未登录状态下访问 `www.douyin.com/search/...` 会被重定向到
    **验证码中间页**，页面里一条业务接口都不会发。这时候干等接口超时纯属浪费，
    识别到拦截应该直接提示「先登录」。
    """
    try:
        title = (await page.title()) or ""
    except Exception:  # noqa: BLE001
        title = ""
    if "验证码" in title or "captcha" in title.lower():
        return "captcha"
    url = getattr(page, "url", "") or ""
    if "verify" in url and "douyin" in url:
        return "captcha"
    for sel in BLOCK_SELECTORS["captcha"]:
        try:
            if await page.locator(sel).count() > 0:
                return "captcha"
        except Exception:  # noqa: BLE001
            continue
    return None


BLOCK_HINTS = {
    "captcha": (
        "被抖音验证码拦截了。未登录状态下，搜索与作品详情接口会触发风控——"
        "请先执行 `dy login` 完成扫码登录，再重试。"
    ),
    "login_wall": "页面要求登录。请先执行 `dy login` 完成扫码登录，再重试。",
}


class BlockedError(RuntimeError):
    """页面被风控拦截（验证码 / 登录墙）。"""


# ---------------------------------------------------------------- 执行上下文


@dataclass
class Ctx:
    """动作层能拿到的东西。"""

    page: Any
    context: Any = None
    settings: Settings = None
    data: dict = field(default_factory=dict)

    # ---- cookie / 登录态 ----

    async def cookies(self) -> dict[str, str]:
        try:
            src = self.context if self.context is not None else self.page.context
            raw = await src.cookies()
        except Exception:  # noqa: BLE001
            return {}
        return {c.get("name", ""): c.get("value", "") for c in raw or []}

    async def is_logged_in(self) -> bool:
        return bool(set(await self.cookies()) & LOGIN_COOKIES)

    # ---- 精确等待 JSON 响应 ----

    async def expect_json(
        self,
        pattern: str | list[str],
        trigger: Callable[[], Awaitable[Any]],
        timeout: float = 25.0,
    ) -> dict | None:
        """在 trigger 执行的同时等待匹配的 JSON 响应并解析出响应体。

        这是绕开抖音 `a_bogus` 签名的关键：**让页面自己发请求，我们只收结果。**
        """
        pats = [pattern] if isinstance(pattern, str) else list(pattern)

        def _match(resp) -> bool:
            if resp.status != 200:
                return False
            return any(p in resp.url for p in pats)

        try:
            async with self.page.expect_response(_match, timeout=int(timeout * 1000)) as info:
                await trigger()
            resp = await info.value
            return await resp.json()
        except Exception as exc:  # noqa: BLE001
            log.debug("等待响应 %s 失败: %s", pats, exc)
            return None

    async def expect_json_after(
        self,
        pattern: str | list[str],
        timeout: float = 25.0,
        settle: float = 0.0,
    ) -> dict | None:
        """等待页面自然发出的请求（不额外触发动作）。

        ⚠️ 必须在请求发出**之前**进入等待，否则响应已经过去了就等不到。
        页面加载时自动发出的请求，请改用 `expect_json(pattern, trigger=ctx.goto)`。
        """
        pats = [pattern] if isinstance(pattern, str) else list(pattern)

        def _match(resp) -> bool:
            if resp.status != 200:
                return False
            return any(p in resp.url for p in pats)

        try:
            async with self.page.expect_response(_match, timeout=int(timeout * 1000)) as info:
                if settle:
                    await self.page.wait_for_timeout(int(settle * 1000))
            resp = await info.value
            return await resp.json()
        except Exception as exc:  # noqa: BLE001
            log.debug("等待响应 %s 失败: %s", pats, exc)
            return None

    # ---- 杂项 ----

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        await self.page.goto(url, wait_until=wait_until, timeout=self.settings.nav_timeout_ms)

    async def screenshot(self, path: str) -> str:
        await self.page.screenshot(path=path, full_page=False)
        return path

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


# ---------------------------------------------------------------- 引擎


@dataclass
class Outcome:
    """一次抓取的产物。"""

    result: Any = None
    data: dict = field(default_factory=dict)
    url: str = ""
    captures: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def markdown(self) -> str:
        md = getattr(self.result, "markdown", None)
        if md is None:
            return ""
        return getattr(md, "raw_markdown", "") or getattr(md, "fit_markdown", "") or ""

    @property
    def links(self) -> list[str]:
        raw = getattr(self.result, "links", None) or {}
        out: list[str] = []
        seen: set[str] = set()
        groups = raw.values() if isinstance(raw, dict) else [raw]
        for group in groups:
            if not isinstance(group, list):
                continue
            for item in group:
                href = item.get("href") if isinstance(item, dict) else item
                if href and href not in seen:
                    seen.add(href)
                    out.append(href)
        return out

    def resolve_captures(self) -> None:
        """把 plan 声明的 `_capture` 模式解析成结构化 JSON。

        为什么不用 `expect_response` 抓页面加载时的接口：
            钩子在导航**之后**才触发，那时首屏接口早就发完了，
            `expect_response` 只能等待**未来**发生的响应。
            而 Crawl4AI 的 `capture_network_requests` 在导航前就挂了监听，
            覆盖整个页面加载周期——所以从它的结果里取。
        """
        patterns = self.data.pop("_capture", None) or []
        for pat in patterns:
            self.captures[pat] = json_bodies(self.result, pat)


def json_bodies(result: Any, pattern: str) -> list[dict]:
    """从 Crawl4AI 捕获的网络事件里取出匹配 pattern 的 JSON 响应体。"""
    out: list[dict] = []
    for e in getattr(result, "network_requests", None) or []:
        if e.get("event_type") != "response":
            continue
        if pattern not in (e.get("url") or ""):
            continue
        body = (e.get("body") or {}).get("text")
        if not body:
            continue
        try:
            data = json.loads(body)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


Plan = Callable[[Ctx], Awaitable[dict]]


class DouyinCrawler:
    """Crawl4AI 会话封装。"""

    def __init__(
        self,
        settings: Settings,
        profile: str | None = None,
        headless: bool | None = None,
    ) -> None:
        self.settings = settings
        self.profile = profile or settings.default_profile
        self.headless = settings.headless if headless is None else headless
        self.settings.ensure_dirs()

    # ---- 配置 ----

    def browser_config(self):
        from crawl4ai import BrowserConfig

        kwargs: dict[str, Any] = {
            "browser_type": "chromium",
            # 用系统真实 Chrome，与用户日常环境一致
            "channel": self.settings.channel or "chrome",
            "headless": self.headless,
            "use_persistent_context": True,
            "user_data_dir": str(self.settings.profile_dir(self.profile)),
            "enable_stealth": True,
            "viewport_width": self.settings.viewport_width,
            "viewport_height": self.settings.viewport_height,
            "extra_args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        ua = native_chrome_ua()
        if ua:
            kwargs["user_agent"] = ua

        return BrowserConfig(**kwargs)

    def run_config(self, **overrides: Any):
        from crawl4ai import CacheMode, CrawlerRunConfig

        kwargs: dict[str, Any] = {
            "cache_mode": CacheMode.BYPASS,
            "wait_until": "domcontentloaded",
            "page_timeout": self.settings.nav_timeout_ms,
            "word_count_threshold": 5,
            "scroll_delay": 0.6,
            "js_code": _HOOK_TRIGGER_JS,
            "simulate_user": True,
            "override_navigator": True,
            "locale": self.settings.locale,
            "timezone_id": self.settings.timezone,
            "verbose": False,
        }
        kwargs.update(overrides)
        return CrawlerRunConfig(**kwargs)

    # ---- 执行 ----

    async def run(
        self,
        url: str,
        plan: Plan | None = None,
        *,
        capture_network: bool = True,
        settle_ms: int = 3000,
        **run_overrides: Any,
    ) -> Outcome:
        """打开页面、执行 plan、返回结果。

        `settle_ms`：没有 plan 时，页面加载后额外等待多久再返回。
            抖音这类 SPA 的业务接口是在首屏渲染后才陆续发出的，
            不等一会儿就抽取，会整个错过它们（捕获监听也会被摘掉）。
        """
        from crawl4ai import AsyncWebCrawler

        holder: dict[str, Any] = {}

        async def _settle(ctx: Ctx) -> dict:
            if settle_ms:
                await ctx.page.wait_for_timeout(settle_ms)
            return {}

        effective_plan = plan if plan is not None else _settle

        async def hook(page, context=None, config=None, **_kw):
            ctx = Ctx(page=page, context=context, settings=self.settings)
            try:
                holder["data"] = await effective_plan(ctx) or {}
            except BlockedError as exc:
                # ⚠️ 千万不要在这里 raise：Crawl4AI 的 _crawl_web 会把钩子里的异常
                # 包成通用 RuntimeError（还附一大段源码上下文），错误类型就丢了。
                # 所以先存下来，等 arun 返回后再抛。
                holder["blocked"] = exc
            except Exception as exc:  # noqa: BLE001
                holder["error"] = exc

        bc = self.browser_config()
        rc = self.run_config(capture_network_requests=capture_network, **run_overrides)

        async with AsyncWebCrawler(config=bc) as crawler:
            crawler.crawler_strategy.set_hook("on_execution_started", hook)
            result = await crawler.arun(url=url, config=rc)

        if "blocked" in holder:
            raise holder["blocked"]
        if "error" in holder:
            raise holder["error"]
        if not getattr(result, "success", False):
            raise RuntimeError(f"抓取失败: {getattr(result, 'error_message', '未知错误')}")

        outcome = Outcome(result=result, data=holder.get("data") or {}, url=url)
        outcome.resolve_captures()
        return outcome

    async def run_many(
        self,
        urls: list[str],
        plan: Plan | None = None,
        *,
        concurrency: int = 3,
        capture_network: bool = True,
        **run_overrides: Any,
    ) -> list[Outcome]:
        """批量并发抓取。"""
        from crawl4ai import AsyncWebCrawler

        holder: dict[str, Any] = {}
        bc = self.browser_config()
        rc = self.run_config(capture_network_requests=capture_network, **run_overrides)

        async def hook(page, context=None, config=None, **_kw):
            ctx = Ctx(page=page, context=context, settings=self.settings)
            if plan is None:
                return
            try:
                holder.setdefault("data", {})[getattr(page, "url", "")] = await plan(ctx) or {}
            except Exception as exc:  # noqa: BLE001
                log.debug("批量执行异常: %s", exc)

        async with AsyncWebCrawler(config=bc) as crawler:
            crawler.crawler_strategy.set_hook("on_execution_started", hook)
            results = await crawler.arun_many(urls, config=rc, semaphore_count=concurrency)

        out: list[Outcome] = []
        for r in results:
            if not getattr(r, "success", False):
                continue
            out.append(Outcome(result=r, data=holder.get("data", {}).get(getattr(r, "url", ""), {})))
        return out


# ---------------------------------------------------------------- 同步桥


def run_sync(coro):
    """Typer 命令是同步的，这里统一桥到 asyncio。"""
    return asyncio.run(coro)


def json_or_none(value: Any) -> dict | None:
    """安全地把响应体转成 dict。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
