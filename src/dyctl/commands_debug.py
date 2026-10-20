"""排障工具：doctor / probe / shot。

`probe` 是维护 selectors.py 的主力工具：它把页面上真实的
`data-e2e` 属性、可见按钮、输入框占位符、以及实际调用的接口一次性列出来，
抖音改版后照着改选择器即可，不需要猜。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import Settings
from .utils import console, get_logger, make_table

log = get_logger(__name__)

# 抽取页面里所有 data-e2e 值
_JS_COLLECT = """
() => {
  const e2e = new Set();
  document.querySelectorAll('[data-e2e]').forEach(el => e2e.add(el.getAttribute('data-e2e')));
  const buttons = [];
  document.querySelectorAll('button, [role=button]').forEach(el => {
    const t = (el.innerText || el.textContent || '').trim().slice(0, 40);
    const r = el.getBoundingClientRect();
    if (t && r.width > 0 && r.height > 0) {
      buttons.push({ text: t, e2e: el.getAttribute('data-e2e') || '', cls: (el.className || '').toString().slice(0, 60) });
    }
  });
  const inputs = [];
  document.querySelectorAll('input, textarea, [contenteditable=true]').forEach(el => {
    const r = el.getBoundingClientRect();
    inputs.push({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      placeholder: el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '',
      e2e: el.getAttribute('data-e2e') || '',
      visible: r.width > 0 && r.height > 0,
    });
  });
  return {
    url: location.href,
    title: document.title,
    e2e: Array.from(e2e),
    buttons: buttons.slice(0, 60),
    inputs: inputs.slice(0, 40),
  };
}
"""


def doctor(settings: Settings) -> dict:
    """环境自检。"""
    checks: list[tuple[str, bool, str]] = []

    py_ok = sys.version_info >= (3, 11)
    checks.append(("Python >= 3.11", py_ok, sys.version.split()[0]))

    try:
        import crawl4ai  # noqa: F401

        ver = getattr(crawl4ai, "__version__", "")
        ver = getattr(ver, "__version__", ver) or "已安装"
        checks.append(("crawl4ai 已安装", True, str(ver)))
    except ImportError:
        checks.append(("crawl4ai 已安装", False, "uv sync --extra crawl"))

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        checks.append(("playwright 已安装", True, "ok"))
    except ImportError:
        checks.append(("playwright 已安装", False, "playwright 未安装"))

    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    has_chrome = chrome.exists()
    checks.append(
        (
            "系统 Chrome（优先使用，反风控更佳）",
            has_chrome,
            str(chrome) if has_chrome else "未安装，将回退到内置 Chromium",
        )
    )

    writable = False
    try:
        settings.ensure_dirs()
        probe_file = settings.home / ".write-test"
        probe_file.write_text("ok")
        probe_file.unlink()
        writable = True
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]写入失败: {exc}[/red]")
    checks.append(("状态目录可写", writable, str(settings.home)))

    profiles = []
    if settings.profiles_dir.exists():
        profiles = [p.name for p in settings.profiles_dir.iterdir() if p.is_dir()]
    checks.append(("已有 profile", bool(profiles), ", ".join(profiles) or "无"))

    table = make_table("环境自检", ["检查项", "结果", "详情"])
    for name, ok, detail in checks:
        table.add_row(name, "[green]✓[/green]" if ok else "[red]✗[/red]", detail)
    console.print(table)

    return {
        "ok": all(ok for _, ok, _ in checks),
        "home": str(settings.home),
        "profiles": profiles,
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
    }


def probe(
    settings: Settings,
    url: str,
    profile: str | None = None,
    headless: bool | None = None,
    shot: bool = True,
    wait_ms: int = 6000,
) -> dict:
    """打开页面并 dump 结构信息，用于维护选择器。

    一次会话搞定：结构快照 + 页面自己发出的接口 + 截图。
    """
    import base64

    from .crawler import DouyinCrawler, run_sync

    crawler = DouyinCrawler(settings, profile=profile, headless=headless)
    console.print(f"[cyan]打开:[/cyan] {url}")

    struct: dict = {"e2e": [], "buttons": [], "inputs": [], "url": url, "title": ""}

    async def _collect(ctx):
        await ctx.page.wait_for_timeout(wait_ms)
        struct.update(await ctx.page.evaluate(_JS_COLLECT))
        struct["title"] = await ctx.page.title()
        if shot:
            raw = await ctx.page.screenshot(full_page=False)
            struct["screenshot_b64"] = raw

    outcome = run_sync(
        crawler.run(url, plan=_collect, capture_network=True, screenshot=shot)
    )

    apis = []
    for e in getattr(outcome.result, "network_requests", None) or []:
        if e.get("event_type") == "response":
            apis.append({"url": e.get("url", ""), "status": e.get("status", "")})

    payload = {
        "url": struct.get("url", url),
        "title": struct.get("title", ""),
        "data_e2e": struct.get("e2e", []),
        "buttons": struct.get("buttons", []),
        "inputs": struct.get("inputs", []),
        "apis": apis[:60],
    }

    console.print()
    console.rule(f"[bold]{payload['title']} — {payload['url']}")

    if payload["data_e2e"]:
        table = make_table("data-e2e 属性", ["值"])
        for v in sorted(set(payload["data_e2e"])):
            table.add_row(v)
        console.print(table)
    else:
        console.print("[yellow]页面没有 data-e2e 属性——可能改版了或未渲染完成。[/yellow]")

    if payload["inputs"]:
        table = make_table("输入框", ["标签", "类型", "占位符", "data-e2e", "可见"])
        for i in payload["inputs"]:
            table.add_row(
                i["tag"], i["type"], i["placeholder"] or "-", i["e2e"] or "-", "是" if i["visible"] else "否"
            )
        console.print(table)

    if payload["buttons"]:
        table = make_table("可见按钮", ["文本", "data-e2e", "class"])
        for b in payload["buttons"]:
            table.add_row(b["text"], b["e2e"] or "-", b["cls"] or "-")
        console.print(table)

    if apis:
        table = make_table("页面调用的 JSON 接口", ["URL", "状态"])
        for a in apis:
            table.add_row(a["url"][:110], str(a["status"]))
        console.print(table)

    b64 = struct.get("screenshot_b64")
    if b64:
        path = settings.artifact_path("probe")
        path.write_bytes(base64.b64decode(b64))
        payload["screenshot"] = str(path)
        console.print(f"\n[green]截图已保存:[/green] {path}")

    console.print(
        "\n[dim]提示：把上面 dump 到的 data-e2e 值填进 "
        "src/dyctl/selectors.py 对应分组即可。[/dim]"
    )
    return payload


def shot(
    settings: Settings,
    url: str,
    out: str | None = None,
    profile: str | None = None,
    headless: bool | None = None,
    full_page: bool = False,
) -> dict:
    """截个图。"""
    import base64

    from .crawler import DouyinCrawler, run_sync

    crawler = DouyinCrawler(settings, profile=profile, headless=headless)
    outcome = run_sync(
        crawler.run(url, plan=None, capture_network=False, screenshot=True, full_page=full_page)
    )
    raw = getattr(outcome.result, "screenshot", None)
    if not raw:
        raise RuntimeError("未拿到截图数据")
    data = base64.b64decode(raw)

    if out:
        path = Path(out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = settings.artifact_path("shot")
    path.write_bytes(data)
    console.print(f"[green]截图已保存:[/green] {path}")
    return {"ok": True, "path": str(path)}
