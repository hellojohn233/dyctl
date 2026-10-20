"""端到端冒烟测试。

覆盖：
    1. 模型归一化（Post / Comment / UserProfile）
    2. JSON 取值工具
    3. Crawl4AI 引擎配置：UA 与 Client Hints 一致性、locale/timezone、钩子触发条件
    4. 钩子机制：能否拿到原始 Playwright page 并执行页面级动作
    5. 实际抓取：Markdown 生成 + 网络接口捕获
    6. 抖音站点可达性
    7. 作品链接选择器（div[href] 而非 a[href]）

不依赖抖音登录态。
运行： uv run python tests/smoke_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dyctl.api import as_list, dig, pick  # noqa: E402
from dyctl.config import Settings  # noqa: E402
from dyctl.models import Comment, Post, UserProfile  # noqa: E402
from dyctl.utils import human_size  # noqa: E402

PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {PASS if ok else FAIL} {name}" + (f"  — {detail}" if detail else ""))


# ---------------------------------------------------------------- 模型解析

SAMPLE_POST = {
    "aweme_id": "7300000000000000001",
    "desc": "手冲咖啡的三个关键参数 #咖啡",
    "create_time": 1720000000,
    "author": {"uid": "111", "nickname": "咖啡实验室"},
    "statistics": {"play_count": 120000, "digg_count": 5600, "comment_count": 320, "share_count": 88},
    "video": {"cover": {"url_list": ["https://p3-pc.douyinpic.com/cover.jpg"]}},
}

SAMPLE_COMMENT = {
    "cid": "7300000000000000002",
    "text": "这期讲得真好",
    "create_time": 1720000100,
    "digg_count": 42,
    "user": {"uid": "222", "nickname": "小王"},
}

SAMPLE_USER = {
    "user": {
        "uid": "111",
        "nickname": "咖啡实验室",
        "unique_id": "coffee_lab",
        "sec_uid": "MS4wLjABAAAAxxxx",
        "follower_count": 125000,
        "following_count": 88,
        "aweme_count": 120,
        "total_favorited": 300000,
        "signature": "每天一杯",
    }
}


def test_models() -> None:
    print("\n\033[1m模型归一化\033[0m")
    post = Post.from_payload(SAMPLE_POST)
    check("Post 解析 aweme_id", post.aweme_id == "7300000000000000001", post.aweme_id)
    check("Post 解析标题", post.title.startswith("手冲咖啡"), post.title)
    check("Post 解析作者", post.author == "咖啡实验室", post.author)
    check(
        "Post 解析统计数据",
        post.stats.play == 120000 and post.stats.like == 5600,
        str(post.stats.as_dict()),
    )
    check("Post 解析封面", post.cover_url.endswith("cover.jpg"), post.cover_url)

    c = Comment.from_payload(SAMPLE_COMMENT)
    check("Comment 解析", c.cid == "7300000000000000002" and c.text == "这期讲得真好", c.text)
    check("Comment 解析点赞", c.like == 42, str(c.like))

    u = UserProfile.from_payload(SAMPLE_USER)
    check("UserProfile 解析", u.nickname == "咖啡实验室" and u.follower == 125000)

    empty = Post.from_payload({})
    check("空 JSON 不崩溃", empty.aweme_id == "" and empty.stats.play == 0)

    check("dig 取嵌套值", dig(SAMPLE_POST, "author", "nickname") == "咖啡实验室")
    check("pick 兜底", pick({}, "a", "b", default="fallback") == "fallback")
    check("as_list 兼容包裹", len(as_list({"aweme_list": [SAMPLE_POST]}, "aweme_list")) == 1)
    check("human_size 格式化", human_size(123456) == "12w", human_size(123456))


# ---------------------------------------------------------------- 引擎配置


def test_engine_config() -> None:
    print("\n\033[1mCrawl4AI 引擎配置\033[0m")
    settings = Settings()
    from dyctl.crawler import DouyinCrawler, native_chrome_ua

    ua = native_chrome_ua()
    check("构造出本机真实 UA", bool(ua) and "Macintosh" in ua, (ua or "")[-32:])

    crawler = DouyinCrawler(settings, profile="smoke", headless=True)
    bc = crawler.browser_config()
    check("BrowserConfig 使用真实 UA", bc.user_agent == ua, bc.user_agent[-32:])
    check("启用持久化 profile", bc.use_persistent_context, str(bc.user_data_dir)[-30:])
    check("启用 stealth", bc.enable_stealth, "enable_stealth")

    hint = bc.headers.get("sec-ch-ua", "")
    ver = ""
    if ua and "Chrome/" in ua:
        ver = ua.split("Chrome/")[1].split(".")[0]
    check("sec-ch-ua 与 UA 版本自洽", bool(ver) and f'v="{ver}"' in hint, hint[:58])

    rc = crawler.run_config()
    check("RunConfig 带 locale", rc.locale == settings.locale, str(rc.locale))
    check("RunConfig 带 timezone", rc.timezone_id == settings.timezone, str(rc.timezone_id))
    check("RunConfig 开启 simulate_user", rc.simulate_user, "模拟鼠标轨迹")
    check("RunConfig 注入了 js_code 触发器", bool(rc.js_code), repr(rc.js_code))


# ---------------------------------------------------------------- 钩子机制


def test_hook_mechanism() -> None:
    """钩子必须能拿到原始 Playwright page——整个动作层的基础。"""
    print("\n\033[1m钩子机制（动作层基础）\033[0m")
    settings = Settings()
    from dyctl.crawler import DouyinCrawler, run_sync

    seen: dict = {}

    async def plan(ctx):
        seen["page_type"] = type(ctx.page).__name__
        seen["url"] = ctx.page.url
        seen["title"] = await ctx.page.title()
        # 页面级动作：填个字段再读回来
        await ctx.page.evaluate("() => { const d=document.createElement('div'); d.id='__probe'; d.textContent='ok'; document.body.appendChild(d); }")
        seen["text"] = await ctx.page.locator("#__probe").inner_text()
        return {"ok": True}

    crawler = DouyinCrawler(settings, profile="smoke", headless=True)
    outcome = run_sync(crawler.run("https://example.com", plan=plan, capture_network=False))
    check("钩子被触发", bool(seen), f"page 类型={seen.get('page_type')}")
    check("拿到的是原始 Playwright Page", seen.get("page_type") == "Page", str(seen.get("page_type")))
    check("页面级动作可执行", seen.get("text") == "ok", str(seen.get("text")))
    check("plan 返回值进入 Outcome", outcome.data.get("ok") is True, str(outcome.data))


# ---------------------------------------------------------------- 实际抓取


def test_crawl() -> None:
    print("\n\033[1m实际抓取\033[0m")
    settings = Settings()
    from dyctl.crawler import DouyinCrawler, run_sync

    crawler = DouyinCrawler(settings, profile="smoke", headless=True)
    outcome = run_sync(
        crawler.run("https://httpbin.org/json", plan=None, capture_network=True)
    )
    md = outcome.markdown
    check("生成 Markdown", len(md) > 20, f"{len(md)} 字符")

    # 网络捕获：0.9.5 的 response 事件带 body
    reqs = getattr(outcome.result, "network_requests", None) or []
    bodies = [
        e for e in reqs if e.get("event_type") == "response" and (e.get("body") or {}).get("text")
    ]
    check("捕获到带 body 的响应", bool(bodies), f"{len(bodies)} 个")
    if bodies:
        try:
            parsed = json.loads(bodies[0]["body"]["text"])
            check("响应体可解析为 JSON", isinstance(parsed, dict), str(list(parsed)[:3]))
        except Exception as exc:  # noqa: BLE001
            check("响应体可解析为 JSON", False, str(exc))


def test_douyin_reachable() -> None:
    print("\n\033[1m抖音站点可达性\033[0m")
    settings = Settings()
    from dyctl.crawler import DouyinCrawler, run_sync

    try:
        crawler = DouyinCrawler(settings, profile="smoke", headless=True)
        outcome = run_sync(crawler.run("https://www.douyin.com/", plan=None, capture_network=True))
        check("www.douyin.com 可访问", bool(outcome.markdown), f"{len(outcome.markdown)} 字符 markdown")
        reqs = getattr(outcome.result, "network_requests", None) or []
        apis = [
            e["url"].split("?")[0]
            for e in reqs
            if e.get("event_type") == "response" and "/aweme/v1/web/" in e.get("url", "")
        ]
        check("捕获到站点接口", bool(apis), f"{len(apis)} 个")
        for u in list(dict.fromkeys(apis))[:6]:
            print(f"      {u}")
    except Exception as exc:  # noqa: BLE001
        check("www.douyin.com 可访问", False, f"{type(exc).__name__}: {exc}")


def test_video_link_selector() -> None:
    """作品链接挂在 div[href] 上，不是 a[href] —— 这是实测踩过的坑。"""
    print("\n\033[1m作品链接选择器\033[0m")
    from dyctl.selectors import VIDEO_LINK

    check("选择器不限定标签", VIDEO_LINK == "[href*='/video/']", VIDEO_LINK)
    check("不会误匹配 a[href]", "a[" not in VIDEO_LINK, "a[href*='/video/'] 在抖音上匹配 0 条")


def main() -> int:
    print("\033[1m═══ dyctl 冒烟测试 ═══\033[0m")
    test_models()
    test_engine_config()
    test_hook_mechanism()
    test_crawl()
    test_douyin_reachable()
    test_video_link_selector()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n\033[1m结果: {passed}/{total} 通过\033[0m")
    failed = [(n, d) for n, ok, d in results if not ok]
    if failed:
        print("\n失败项：")
        for n, d in failed:
            print(f"  - {n} {d}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
