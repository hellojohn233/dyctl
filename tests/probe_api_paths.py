"""校准脚本：抓取抖音搜索页与视频页的真实接口路径。

用来把 crawler.py 里的 API 路径常量校准成实测值。
运行： uv run python tests/probe_api_paths.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dyctl.config import Settings  # noqa: E402
from dyctl.crawler import DouyinCrawler, run_sync  # noqa: E402

AWEME_PATHS = ("/aweme/v1/web/", "/aweme/v1/")


def show(title: str, result) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
    reqs = getattr(result, "network_requests", None) or []
    seen = set()
    for e in reqs:
        if e.get("event_type") != "response":
            continue
        path = e.get("url", "").split("?")[0]
        if any(p in path for p in AWEME_PATHS) and path not in seen:
            seen.add(path)
            body = (e.get("body") or {}).get("text") or ""
            print(f"  {path}   [body {len(body)} 字符]")


def main() -> int:
    settings = Settings()
    aweme_id = None

    crawler = DouyinCrawler(settings, profile="api-probe", headless=True)

    # ---- 1. 搜索页 ----
    outcome = run_sync(
        crawler.run(
            "https://www.douyin.com/search/%E5%92%96%E5%95%A1?type=video",
            plan=None,
            capture_network=True,
        )
    )
    show("搜索页接口", outcome.result)

    # 从捕获的响应体里抠一个真实 aweme_id
    reqs = getattr(outcome.result, "network_requests", None) or []
    for e in reqs:
        if e.get("event_type") != "response":
            continue
        body = (e.get("body") or {}).get("text") or ""
        if "aweme" not in body:
            continue
        m = re.search(r'"aweme_id"\s*:\s*"?(\d{15,25})"?', body)
        if m:
            aweme_id = m.group(1)
            print(f"\n[样本] 从响应体提取到 aweme_id = {aweme_id}")
            break

    if not aweme_id:
        print("\n[!] 没找到 aweme_id（未登录时搜索接口会触发验证码），跳过视频页探测")

    # ---- 2. 视频页 ----
    if aweme_id:
        outcome2 = run_sync(
            crawler.run(
                f"https://www.douyin.com/video/{aweme_id}",
                plan=None,
                capture_network=True,
            )
        )
        show(f"视频页接口 (aweme_id={aweme_id})", outcome2.result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
