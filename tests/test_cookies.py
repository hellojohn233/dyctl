"""Cookie 文件解析与注入测试。

覆盖 4 种格式（Netscape txt / JSON 数组 / JSON 对象 / Playwright storage_state）
以及真实注入：把 cookie 喂进浏览器，确认浏览器里真的有了。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dyctl.cookies import (  # noqa: E402
    CookieParseError,
    describe,
    export_cookies,
    filter_douyin,
    parse_cookie_file,
)

PASS, FAIL = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {PASS if ok else FAIL} {name}" + (f"  — {detail}" if detail else ""))


NETSCAPE = """# Netscape HTTP Cookie File
# This is a generated file! Do not edit.

#HttpOnly_.douyin.com\tTRUE\t/\tTRUE\t1793696400\tsessionid\tabc123%3D%3D
.douyin.com\tTRUE\t/\tFALSE\t0\tsessionid_ss\tdef456
.douyin.com\tTRUE\t/\tFALSE\t0\tsid_tt\tghi789
.example.com\tTRUE\t/\tFALSE\t0\tother_site\tzzz
"""

JSON_LIST = json.dumps(
    [
        {
            "domain": ".douyin.com",
            "expirationDate": 1793696400,
            "httpOnly": True,
            "name": "sessionid",
            "path": "/",
            "sameSite": "no_restriction",
            "secure": True,
            "value": "abc123",
        },
        {
            "domain": ".douyin.com",
            "name": "sessionid_ss",
            "path": "/",
            "value": "def456",
        },
    ]
)

JSON_DICT = json.dumps({"sessionid": "abc123", "sid_tt": "ghi789"})

STORAGE_STATE = json.dumps(
    {
        "cookies": [
            {
                "name": "sessionid",
                "value": "abc123",
                "domain": ".douyin.com",
                "path": "/",
                "expires": 1793696400,
                "httpOnly": True,
                "secure": True,
            }
        ],
        "origins": [],
    }
)


def _write(tmp: Path, name: str, content: str) -> Path:
    p = tmp / name
    p.write_text(content, encoding="utf-8")
    return p


def test_formats(tmp: Path) -> None:
    print("\n\033[1mCookie 文件格式解析\033[0m")

    # 1. Netscape
    c = parse_cookie_file(_write(tmp, "a.txt", NETSCAPE))
    names = {x["name"] for x in c}
    check("Netscape txt 解析出 4 个 cookie", len(c) == 4, f"{len(c)} 个")
    check("Netscape txt 识别 #HttpOnly_ 前缀", "sessionid" in names, str(sorted(names)))
    check("Netscape txt 保留 value", any(x["name"] == "sessionid" and x["value"] == "abc123%3D%3D" for x in c))
    check("Netscape txt 会话 cookie → expires=-1", any(x["name"] == "sid_tt" and x["expires"] == -1 for x in c))
    check("Netscape txt 域名正确", any(x["name"] == "sessionid" and x["domain"] == ".douyin.com" for x in c))

    # 2. JSON 数组
    c2 = parse_cookie_file(_write(tmp, "b.json", JSON_LIST))
    check("JSON 数组解析出 2 个", len(c2) == 2, f"{len(c2)} 个")
    check("JSON 数组 sameSite 归一化", any(x.get("sameSite") == "None" for x in c2), str(c2[0].get("sameSite")))
    check("JSON 数组 expirationDate → expires", any(x["name"] == "sessionid" and x["expires"] == 1793696400 for x in c2))

    # 3. JSON 对象
    c3 = parse_cookie_file(_write(tmp, "c.json", JSON_DICT))
    check("JSON 对象解析出 2 个", len(c3) == 2, f"{len(c3)} 个")
    check("JSON 对象补默认域名", all(x["domain"] == ".douyin.com" for x in c3))

    # 4. storage_state
    c4 = parse_cookie_file(_write(tmp, "d.json", STORAGE_STATE))
    check("storage_state 解析出 1 个", len(c4) == 1, f"{len(c4)} 个")

    # 5. 域名过滤
    kept, dropped = filter_douyin(c)
    check("过滤掉非抖音 cookie", dropped == 1 and len(kept) == 3, f"保留 {len(kept)} / 丢弃 {dropped}")

    # 6. 错误处理
    try:
        parse_cookie_file(_write(tmp, "e.txt", ""))
        check("空文件报错", False, "没有抛异常")
    except CookieParseError:
        check("空文件报错", True, "CookieParseError")

    try:
        parse_cookie_file(tmp / "notexist.txt")
        check("文件不存在报错", False, "没有抛异常")
    except FileNotFoundError:
        check("文件不存在报错", True, "FileNotFoundError")

    # 7. 描述
    d = describe(c)
    check("describe 识别登录态 cookie", "登录态" in d, d[:60])

    # 8. 导出
    out = export_cookies(c, tmp / "out.txt")
    check("导出 cookies.txt", out.exists() and out.stat().st_size > 0, str(out))
    re_parsed = parse_cookie_file(out)
    check("导出的文件能再解析回来", len(re_parsed) == len(c), f"{len(re_parsed)} 个")


def test_injection() -> None:
    """真把 cookie 喂进浏览器，确认它真的到了。"""
    print("\n\033[1mCookie 真实注入\033[0m")
    from dyctl.config import Settings
    from dyctl.crawler import DouyinCrawler, run_sync

    tmp = Path(tempfile.mkdtemp())
    cf = _write(tmp, "inj.txt", NETSCAPE)

    settings = Settings()
    crawler = DouyinCrawler(settings, profile="cookie-probe", headless=True, cookie_file=str(cf))
    injected: dict = {}

    async def plan(ctx):
        injected["cookies"] = await ctx.all_cookies()
        return {"ok": True}

    try:
        run_sync(crawler.run("https://example.com", plan=plan, capture_network=False))
        got = {c["name"]: c["value"] for c in injected.get("cookies", [])}
        check("浏览器里出现了 sessionid", "sessionid" in got, str(sorted(got)[:6]))
        check("cookie 值正确", got.get("sessionid") == "abc123%3D%3D", str(got.get("sessionid")))
        check("非抖音 cookie 被过滤掉", "other_site" not in got, str(sorted(got)[:6]))
    except Exception as exc:  # noqa: BLE001
        check("注入成功", False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    print("\033[1m═══ dyctl cookie 测试 ═══\033[0m")
    with tempfile.TemporaryDirectory() as d:
        test_formats(Path(d))
    test_injection()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n\033[1m结果: {passed}/{len(results)} 通过\033[0m")
    failed = [(n, d) for n, ok, d in results if not ok]
    if failed:
        print("\n失败项：")
        for n, d in failed:
            print(f"  - {n} {d}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
