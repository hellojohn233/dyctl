"""动作层：每个抖音操作都是一个 Crawl4AI plan。

Plan 就是一个 `async def plan(ctx) -> dict`：
    - `ctx.page` 是原始 Playwright page（由 on_execution_started 钩子给出）
    - `ctx.expect_json(...)` 精确等待并解析页面自己发出的 JSON 响应
    - 返回的 dict 会成为命令的输出数据

写操作直接用 `ctx.page`（file upload / fill / click），
读操作优先用 `ctx.expect_json`（拿结构化数据，比解析 DOM 稳）。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .api import as_list
from .crawler import API, URLS, BlockedError, Ctx, detect_block
from .models import Comment, Post, UserProfile
from .selectors import (
    COMMENT,
    LOGIN,
    PUBLISH,
    VIDEO_LINK,
)
from .utils import get_logger, human_pause

log = get_logger(__name__)

# 作品列表接口的候选路径（不同版本/站点有差异）
POST_LIST_PATTERNS = [API["post_list"], "/aweme/v1/web/post/list/"]
DATA_PATTERNS = [API["data_overview"], "/aweme/v1/web/data/"]
COMMENT_PATTERNS = [API["comment_list"]]
SEARCH_PATTERNS = [API["search_single"], API["search_item"], "/aweme/v1/web/general/search/"]
PROFILE_PATTERNS = [API["user_profile"], "/aweme/v1/web/user/profile/", "/aweme/v1/web/user/detail/"]


async def _first_visible(page, selectors, timeout: int = 8000):
    """按顺序尝试候选选择器，返回第一个可见的（异步版）。"""
    waited = 0
    step = 250
    while waited <= timeout:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    return loc
            except Exception:  # noqa: BLE001
                continue
        await page.wait_for_timeout(step)
        waited += step
    return None


async def _click_first(page, selectors, timeout: int = 8000) -> bool:
    loc = await _first_visible(page, selectors, timeout)
    if loc is None:
        return False
    await loc.click()
    return True


async def _fill_first(page, selectors, text: str, timeout: int = 8000) -> bool:
    loc = await _first_visible(page, selectors, timeout)
    if loc is None:
        return False
    try:
        await loc.fill(text)
    except Exception:  # noqa: BLE001 - contenteditable 用 fill 偶发失败
        await loc.click()
        await page.keyboard.type(text, delay=25)
    return True


async def _exists(page, selectors) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def _guard_block(ctx: Ctx) -> None:
    reason = await detect_block(ctx.page)
    if reason:
        from .crawler import BLOCK_HINTS

        raise BlockedError(BLOCK_HINTS.get(reason, "页面被风控拦截，请重试。"))


# ---------------------------------------------------------------- 登录


async def plan_login(ctx: Ctx) -> dict:
    """打开抖音，等用户扫码。"""
    await ctx.goto(URLS["login"])
    await ctx.page.wait_for_timeout(2500)

    if await ctx.is_logged_in():
        return {"ok": True, "already_logged_in": True}

    qr = await _first_visible(ctx.page, LOGIN["qrcode"], timeout=15000)
    if qr is None:
        log.warning("未识别到登录二维码，可能是页面改版或需先关闭弹窗")
    else:
        log.info("请用抖音 App 扫描浏览器窗口中的二维码")

    deadline = time.time() + ctx.settings.login_wait_seconds
    while time.time() < deadline:
        if await ctx.is_logged_in():
            return {"ok": True, "already_logged_in": False}
        await ctx.page.wait_for_timeout(1000)

    return {"ok": False, "reason": "等待扫码超时"}


async def plan_status(ctx: Ctx) -> dict:
    """检查登录态。"""
    await ctx.goto(URLS["home"])
    await ctx.page.wait_for_timeout(1500)
    cookies = await ctx.cookies()
    return {
        "logged_in": bool(set(cookies) & {"sessionid", "sessionid_ss", "sid_tt"}),
        "cookie_names": sorted(cookies),
    }


# ---------------------------------------------------------------- 发布


async def plan_publish(ctx: Ctx, video: str, body: str, cover: str | None, schedule: str | None) -> dict:
    """上传视频 → 填简介 → 封面/定时 → 发布。"""
    page = ctx.page
    await ctx.goto(URLS["upload"])
    await page.wait_for_timeout(3000)
    await _guard_block(ctx)

    if not await ctx.is_logged_in():
        raise PermissionError("未登录或登录态已失效。请先执行 `dy login`。")

    # 1. 选择文件
    file_input = await _first_visible(page, PUBLISH["file_input"], timeout=20000)
    if file_input is None:
        raise RuntimeError(
            "未找到文件上传控件。上传页可能改版——"
            "请运行 `dy probe https://creator.douyin.com/creator-micro/content/upload` "
            "并更新 selectors.py 的 PUBLISH['file_input']。"
        )
    await file_input.set_input_files(video)

    # 2. 等上传/转码完成（简介输入框出现即代表表单就绪）
    title_loc = await _first_visible(page, PUBLISH["title_input"], timeout=180_000)
    if title_loc is None:
        shot = ctx.settings.artifact_path(f"publish-timeout-{int(time.time())}")
        await page.screenshot(path=str(shot))
        raise RuntimeError(
            f"等待简介输入框超时（可能上传失败或被限流）。现场截图: {shot}\n"
            "可运行 `dy probe <上传页 URL>` 排查选择器。"
        )

    # 3. 填简介与话题
    if body:
        await human_pause()
        await title_loc.click()
        try:
            await title_loc.fill(body)
        except Exception:  # noqa: BLE001
            await title_loc.click()
            await page.keyboard.type(body, delay=25)

    # 4. 封面（可选）
    if cover and Path(cover).exists():
        await _set_cover(ctx, cover)
    elif cover:
        log.warning("封面文件不存在，跳过: %s", cover)

    # 5. 定时发布（可选）
    if schedule:
        await _click_first(page, PUBLISH["schedule_tab"], timeout=8000)
        await page.wait_for_timeout(1500)

    # 6. 点击发布
    publish_btn = await _first_visible(page, PUBLISH["publish_button"], timeout=15000)
    if publish_btn is None:
        shot = ctx.settings.artifact_path(f"publish-button-missing-{int(time.time())}")
        await page.screenshot(path=str(shot))
        raise RuntimeError(
            f"未找到发布按钮。现场截图: {shot}\n"
            "请用 `dy probe` 检查 selectors.py 的 PUBLISH['publish_button']。"
        )
    await human_pause()
    await publish_btn.click()

    ok = await _wait_publish_result(ctx)
    return {"published": ok, "url": page.url, "body": body}


async def _set_cover(ctx: Ctx, cover: str) -> None:
    page = ctx.page
    try:
        trigger = await _first_visible(page, PUBLISH["cover_add"], timeout=8000)
        if trigger is None:
            log.warning("未找到封面入口，跳过封面设置")
            return
        await trigger.click()
        await page.wait_for_timeout(1500)
        inputs = page.locator("input[type='file']")
        if await inputs.count() > 0:
            await inputs.last.set_input_files(cover)
            await page.wait_for_timeout(2000)
    except Exception as exc:  # noqa: BLE001
        log.warning("封面设置失败（不影响发布）: %s", exc)


async def _wait_publish_result(ctx: Ctx) -> bool:
    page = ctx.page
    deadline = time.time() + 60
    while time.time() < deadline:
        url = page.url
        if "content/manage" in url:
            return True
        if await _exists(page, PUBLISH["publish_success"]):
            return True
        await page.wait_for_timeout(1000)
    return False


# ---------------------------------------------------------------- 内容


async def interact_posts(ctx: Ctx, scroll_rounds: int) -> dict:
    """创作者中心作品列表：翻页触发更多请求。"""
    page = ctx.page
    await page.wait_for_timeout(3000)
    await _guard_block(ctx)

    for _ in range(max(0, scroll_rounds)):
        await page.mouse.wheel(0, 2400)
        await page.wait_for_timeout(1800)

    return {"_capture": POST_LIST_PATTERNS}


def extract_posts(outcome, limit: int) -> dict:
    """把捕获到的作品列表响应合并成作品数组。"""
    items: list[dict] = []
    for bodies in outcome.captures.values():
        for body in bodies:
            items.extend(as_list(body, "aweme_list", "post_list"))
    posts = [Post.from_payload(i) for i in items]
    posts = [p for p in posts if p.aweme_id]
    # 去重（翻页会重复返回）
    seen: set[str] = set()
    uniq = []
    for p in posts:
        if p.aweme_id in seen:
            continue
        seen.add(p.aweme_id)
        uniq.append(p)
    return {"posts": [p.as_dict() for p in uniq[:limit]], "count": len(uniq)}


async def interact_metrics(ctx: Ctx) -> dict:
    """创作者中心数据概览。"""
    await ctx.page.wait_for_timeout(4000)
    await _guard_block(ctx)
    return {"_capture": DATA_PATTERNS}


def extract_metrics(outcome) -> dict:
    for bodies in outcome.captures.values():
        if bodies:
            return {"ok": True, "data": bodies[0]}
    return {"ok": False, "data": None}


async def interact_profile(ctx: Ctx) -> dict:
    """当前账号资料。"""
    await ctx.page.wait_for_timeout(3500)
    return {"_capture": PROFILE_PATTERNS}


def extract_profile(outcome) -> dict:
    for bodies in outcome.captures.values():
        if bodies:
            user = UserProfile.from_payload(bodies[0])
            return {"ok": True, "profile": user.as_dict()}
    return {"ok": False, "reason": "未捕获到用户资料接口（可能未登录或接口变更）"}


# ---------------------------------------------------------------- 互动


async def interact_comments(ctx: Ctx, aweme_id: str) -> dict:
    """作品评论列表：滚动加载更多评论。"""
    page = ctx.page
    await page.wait_for_timeout(3500)
    await _guard_block(ctx)

    for _ in range(3):
        await page.mouse.wheel(0, 1800)
        await page.wait_for_timeout(1400)

    return {"_capture": COMMENT_PATTERNS}


def extract_comments(outcome, limit: int) -> dict:
    items: list[dict] = []
    for bodies in outcome.captures.values():
        for body in bodies:
            items.extend(as_list(body, "comments"))
    comments = [Comment.from_payload(i) for i in items]
    seen: set[str] = set()
    uniq = []
    for c in comments:
        if c.cid in seen:
            continue
        seen.add(c.cid)
        uniq.append(c)
    return {"comments": [c.as_dict() for c in uniq[:limit]], "count": len(uniq)}


async def plan_reply(ctx: Ctx, aweme_id: str, text: str, cid: str | None) -> dict:
    """发表评论或回复某条评论。"""
    page = ctx.page
    url = f"https://www.douyin.com/video/{aweme_id}"
    await ctx.goto(url)
    await page.wait_for_timeout(4000)
    await _guard_block(ctx)

    if not await ctx.is_logged_in():
        raise PermissionError("未登录，无法评论。请先执行 `dy login`。")

    if cid:
        if not await _locate_comment(page, cid):
            raise RuntimeError(
                f"未能在页面中找到评论 {cid}。"
                "可能该评论不在首屏，请用 `dy comments` 抓取列表确认 cid。"
            )

    await human_pause()
    if not await _fill_first(page, COMMENT["input"], text, timeout=12000):
        shot = ctx.settings.artifact_path(f"comment-input-missing-{int(time.time())}")
        await page.screenshot(path=str(shot))
        raise RuntimeError(
            f"未找到评论输入框。现场截图: {shot}\n"
            "请用 `dy probe` 检查 selectors.py 的 COMMENT['input']。"
        )

    await human_pause()
    if not await _click_first(page, COMMENT["send_button"], timeout=6000):
        await page.keyboard.press("Enter")
    await page.wait_for_timeout(2500)

    cleared = True
    inp = await _first_visible(page, COMMENT["input"], timeout=3000)
    if inp is not None:
        cleared = (await inp.inner_text() or "").strip() == ""
    return {"sent": True, "input_cleared": cleared, "text": text}


async def _locate_comment(page, cid: str) -> bool:
    items = page.locator("[data-e2e='comment-item'], div[class*='comment-item']")
    count = await items.count()
    for i in range(count):
        item = items.nth(i)
        try:
            html = (await item.inner_html() or "")[:4000]
        except Exception:  # noqa: BLE001
            continue
        if cid in html:
            await item.scroll_into_view_if_needed()
            await page.wait_for_timeout(600)
            await _click_first(page, COMMENT["reply_button"], timeout=4000)
            await page.wait_for_timeout(1000)
            return True
    return False


# ---------------------------------------------------------------- 检索


async def interact_search(ctx: Ctx, query: str, kind: str) -> dict:
    """搜索视频或用户。"""
    page = ctx.page
    await page.wait_for_timeout(3500)
    await _guard_block(ctx)

    for _ in range(2):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(1500)

    return {"_capture": SEARCH_PATTERNS}


def extract_search(outcome, limit: int) -> dict:
    items: list[dict] = []
    for bodies in outcome.captures.values():
        for body in bodies:
            items.extend(as_list(body, "data", "aweme_list", "result_list"))
    posts = [Post.from_payload(i) for i in items]
    return {"results": [p.as_dict() for p in posts[:limit]], "count": len(posts)}


async def interact_user_posts(ctx: Ctx, sec_uid: str) -> dict:
    """指定用户的作品列表。"""
    page = ctx.page
    await page.wait_for_timeout(3500)
    await _guard_block(ctx)

    for _ in range(2):
        await page.mouse.wheel(0, 2000)
        await page.wait_for_timeout(1500)

    return {"_capture": [API["user_post"], "/aweme/v1/web/post/"]}


def extract_user_posts(outcome, limit: int) -> dict:
    items: list[dict] = []
    for bodies in outcome.captures.values():
        for body in bodies:
            items.extend(as_list(body, "aweme_list", "post_list"))
    posts = [Post.from_payload(i) for i in items]
    return {"posts": [p.as_dict() for p in posts[:limit]], "count": len(posts)}


async def plan_resolve_video_ids(ctx: Ctx, limit: int, scroll_rounds: int) -> dict:
    """从当前页面解析作品 ID 列表。

    实测：作品链接挂在 `<div href="//www.douyin.com/video/xxx">` 上，**不是 `<a href>`**，
    所以 `a[href*='/video/']` 一条都匹配不到，必须用不带标签限定的写法。
    """
    page = ctx.page
    for _ in range(max(0, scroll_rounds)):
        await page.mouse.wheel(0, 2200)
        await page.wait_for_timeout(1400)

    hrefs = await page.eval_on_selector_all(
        VIDEO_LINK, "els => els.map(e => e.getAttribute('href') || '')"
    )
    ids: list[str] = []
    seen: set[str] = set()
    for href in hrefs or []:
        m = re.search(r"/video/(\d+)", href or "")
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            ids.append(m.group(1))
    return {"ids": ids[:limit], "count": len(ids)}
