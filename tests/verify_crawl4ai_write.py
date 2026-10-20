"""验证 Crawl4AI 能不能做「写」操作。

方式：用 on_execution_started 钩子拿到原始 Playwright page，
在本地测试服务器的表单页上填字段 + 点提交，然后检查服务端是否收到 POST。

如果这里能跑通，说明 Crawl4AI 完全可以做写操作——
包括文件上传（page.set_input_files），只是没有现成指令、要手写钩子。

这个脚本是「选型结论」的证据，改架构前建议先跑一遍。
运行： uv run python tests/verify_crawl4ai_write.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_server import start_server  # noqa: E402

from dyctl.config import Settings  # noqa: E402
from dyctl.crawler import DouyinCrawler, run_sync  # noqa: E402

PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {PASS if ok else FAIL} {name}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    settings = Settings()
    submitted: dict = {}

    async def plan(ctx):
        """钩子拿到的就是原始 Playwright page。"""
        page = ctx.page
        await page.wait_for_selector("input[name='custname']", timeout=15000)
        await page.fill("input[name='custname']", "一拳")
        await page.fill("input[name='custtel']", "13800138000")
        # size 是 radio 组
        await page.check("input[name='size'][value='large']")
        # 按钮是 <button>Submit order</button>，没有 type 属性
        # （HTML 默认就是 submit），所以 button[type='submit'] 匹配不到。
        await page.click("form button")
        await page.wait_for_load_state("networkidle")
        submitted["html"] = await page.content()

    base, server = start_server()
    crawler = DouyinCrawler(settings, profile="write-probe", headless=True)
    try:
        outcome = run_sync(crawler.run(f"{base}/form", plan=plan, capture_network=True))
    finally:
        server.shutdown()

    print("\n\033[1mCrawl4AI 写操作验证\033[0m")
    html = submitted.get("html", "")
    # 服务端回显的是 JSON，非 ASCII 被转义成 \uXXXX
    escaped = json.dumps("一拳")[1:-1]

    check("钩子拿到原始 page 并完成填表+提交", bool(html), f"提交后页面 {len(html)} 字符")
    check("提交内容已写入服务端（文本）", "一拳" in html or escaped in html, escaped)
    check("提交内容已写入服务端（电话）", "13800138000" in html)
    check("下拉框选择生效", "large" in html)

    # 抓到的网络事件里应该能看到那次 POST
    reqs = getattr(outcome.result, "network_requests", None) or []
    posts = [e for e in reqs if e.get("event_type") == "request" and e.get("method") == "POST"]
    check("捕获到 POST 请求", bool(posts), f"{len(posts)} 个 POST")
    if posts:
        pd = posts[0].get("post_data") or ""
        check("POST 携带了表单字段", "custname" in pd, pd[:60])

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n\033[1m结果: {passed}/{len(results)} 通过\033[0m")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
