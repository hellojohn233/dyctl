"""内容层：抓取抖音页面 → LLM-ready Markdown → 可选 LLM 分析。

与动作层（actions.py）的分工：
    actions.py   写操作 + 结构化数据（走接口拿 JSON）
    digest.py    内容语义层——把页面变成 LLM 能直接消化的 Markdown

两者共用 crawler.py 的引擎与同一份浏览器 profile，登录态无缝共享。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .config import Settings
from .utils import console, get_logger, now_stamp

log = get_logger(__name__)

VIDEO_URL_RE = re.compile(r"^https?://(?:www\.)?douyin\.com/video/(\d+)")


# ---------------------------------------------------------------- 抓取


def digest_page(
    settings: Settings,
    url: str,
    profile: str | None = None,
    headless: bool | None = None,
    wait_for: str | None = None,
    full_page: bool = False,
    screenshot: bool = False,
) -> dict:
    """抓一个页面，返回 Markdown 与元信息。"""
    from .crawler import DouyinCrawler, run_sync

    crawler = DouyinCrawler(settings, profile=profile, headless=headless)
    overrides: dict[str, Any] = {"scan_full_page": full_page}
    if wait_for:
        overrides["wait_for"] = wait_for
    if screenshot:
        overrides["screenshot"] = True

    outcome = run_sync(crawler.run(url, plan=None, capture_network=False, **overrides))
    markdown = outcome.markdown
    links = outcome.links
    return {
        "url": url,
        "final_url": getattr(outcome.result, "url", url),
        "markdown": markdown,
        "chars": len(markdown),
        "links": links,
        "videos": [u for u in links if VIDEO_URL_RE.match(u)],
        "screenshot": bool(getattr(outcome.result, "screenshot", None)),
        "status_code": getattr(outcome.result, "status_code", None),
    }


def collect_markdown(
    settings: Settings,
    urls: list[str],
    profile: str | None = None,
    headless: bool | None = None,
    concurrency: int = 3,
) -> list[dict]:
    """并发批量把一批作品页转成 Markdown。"""
    from .crawler import DouyinCrawler, run_sync

    crawler = DouyinCrawler(settings, profile=profile, headless=headless)
    outcomes = run_sync(
        crawler.run_many(urls, plan=None, concurrency=concurrency, capture_network=False)
    )
    return [
        {"url": getattr(o.result, "url", ""), "markdown": o.markdown, "chars": len(o.markdown)}
        for o in outcomes
    ]


# ---------------------------------------------------------------- LLM 分析


def _llm_setup() -> dict:
    """从环境变量读取 LLM 配置。LiteLLM 会自动识别各厂商自己的环境变量。"""
    return {
        "provider": os.environ.get("DY_LLM_PROVIDER", "openai/gpt-4o-mini"),
        "api_key": os.environ.get("DY_LLM_API_KEY"),
        "base_url": os.environ.get("DY_LLM_BASE_URL"),
        "model": os.environ.get("DY_LLM_MODEL"),
    }


def analyze_page(
    settings: Settings,
    url: str,
    task: str,
    profile: str | None = None,
    headless: bool | None = None,
    max_chars: int = 40000,
) -> dict:
    """抓取页面 → 交给 LLM 按指定任务分析。"""
    try:
        from litellm import completion
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("需要 litellm（随 crawl4ai 一起安装）") from exc

    page = digest_page(settings, url, profile=profile, headless=headless)
    markdown = page["markdown"]
    if not markdown.strip():
        raise RuntimeError("页面没有可分析的正文内容（可能是空页面或被拦截）")

    setup = _llm_setup()
    truncated = markdown[:max_chars]

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个抖音内容分析助手。基于给定的抖音页面内容回答用户任务，"
                "用中文回答，结论先行，必要时引用原文片段。"
                "如果内容不足以回答，明确说明缺什么，不要编造。"
            ),
        },
        {
            "role": "user",
            "content": f"任务：{task}\n\n--- 页面内容 ---\n{truncated}",
        },
    ]

    kwargs: dict[str, Any] = {
        "model": setup["model"] or setup["provider"],
        "messages": messages,
    }
    if setup["api_key"]:
        kwargs["api_key"] = setup["api_key"]
    if setup["base_url"]:
        kwargs["base_url"] = setup["base_url"]

    try:
        resp = completion(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"LLM 调用失败: {type(exc).__name__}: {exc}\n"
            "请检查 DY_LLM_PROVIDER / DY_LLM_API_KEY 等环境变量配置。"
        ) from exc

    return {
        "url": page["final_url"],
        "task": task,
        "provider": kwargs["model"],
        "source_chars": len(markdown),
        "answer": resp.choices[0].message.content or "",
    }


# ---------------------------------------------------------------- 工具


def video_urls(aweme_ids: list[str]) -> list[str]:
    """aweme_id → 作品页 URL。"""
    return [f"https://www.douyin.com/video/{aid}" for aid in aweme_ids if aid]


def save_markdown(settings: Settings, url: str, markdown: str) -> Path:
    """把 Markdown 存到 artifacts 目录。"""
    settings.ensure_dirs()
    stem = re.sub(r"[^\w一-鿿-]+", "_", url)[-80:]
    path = settings.artifacts_dir / f"digest-{now_stamp()}-{stem}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def print_digest(payload: dict, preview_chars: int = 3000) -> None:
    md = payload.get("markdown", "")
    console.print(f"[bold]URL:[/bold] {payload.get('final_url') or payload.get('url')}")
    console.print(f"[bold]字数:[/bold] {payload.get('chars', len(md))}")
    if payload.get("videos"):
        console.print(f"[bold]页面内作品链接:[/bold] {len(payload['videos'])} 个")
    console.rule("[bold]Markdown 预览")
    console.print(md[:preview_chars] + ("\n…（已截断）" if len(md) > preview_chars else ""))


def dump_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
