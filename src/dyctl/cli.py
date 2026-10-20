"""Typer 命令行入口。

    dy <command> [options]

全局选项：--profile / --headless / --slow-mo / --verbose / --json

每个命令 = 一个 Crawl4AI plan（见 actions.py）+ 一次会话执行。
"""

import functools
import sys
import time

import typer

from . import __version__
from .config import load_settings
from .utils import console, emit_json, setup_logging

app = typer.Typer(
    name="dy",
    help="抖音网页端自动化 CLI（Crawl4AI + Playwright 驱动）",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

profile_app = typer.Typer(help="账号相关命令", no_args_is_help=True)
app.add_typer(profile_app, name="profile")

cookies_app = typer.Typer(help="cookie 导入导出", no_args_is_help=True)
app.add_typer(cookies_app, name="cookies")


# ---------------------------------------------------------------- 全局回调


@app.callback()
def main(
    ctx: typer.Context,
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="浏览器 profile 名（多账号隔离），默认 default"
    ),
    headless: bool | None = typer.Option(
        None, "--headless/--headed", help="无头模式；默认有头（无头极易被风控识别）"
    ),
    slow_mo: int | None = typer.Option(
        None, "--slow-mo", help="每个动作延迟毫秒数，用于降低行为速度"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="输出调试日志"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出结果，便于脚本消费"),
    cookie_file: str | None = typer.Option(
        None,
        "--cookie-file",
        "-c",
        help="用这个 cookie 文件登录（支持 cookies.txt / JSON），免扫码",
    ),
) -> None:
    setup_logging(verbose)
    settings = load_settings()
    if profile:
        settings.default_profile = profile
    if headless is not None:
        settings.headless = headless
    if slow_mo is not None:
        settings.slow_mo = slow_mo
    ctx.obj = {
        "settings": settings,
        "profile": profile,
        "json": as_json,
        "cookie_file": cookie_file,
    }


def _settings(ctx: typer.Context):
    return ctx.obj["settings"]


def _emit(ctx: typer.Context, payload) -> None:
    if ctx.obj and ctx.obj.get("json"):
        emit_json(payload)


def _crawler(ctx: typer.Context):
    from .crawler import DouyinCrawler

    return DouyinCrawler(
        _settings(ctx),
        profile=ctx.obj["profile"],
        cookie_file=ctx.obj.get("cookie_file"),
    )


def _run(ctx: typer.Context, url: str, plan, extract=None, **kwargs):
    """执行一个 plan，返回 Outcome。

    `plan` 负责页面级交互（点击/滚动/上传），
    `extract` 负责从捕获到的网络响应里解析结构化数据。
    """
    from .crawler import run_sync

    crawler = _crawler(ctx)
    outcome = run_sync(crawler.run(url, plan, **kwargs))
    if extract is not None:
        outcome.data = extract(outcome)
    return outcome


# ---------------------------------------------------------------- 账号


@app.command("login")
def cmd_login(
    ctx: typer.Context,
    wait: int = typer.Option(180, "--wait", help="等待扫码的秒数"),
    cookie_file: str | None = typer.Option(
        None, "--cookie-file", "-c", help="从 cookie 文件导入登录态，免扫码"
    ),
) -> None:
    """登录抖音：扫码，或从 cookie 文件导入。"""
    from .actions import plan_login

    _settings(ctx).login_wait_seconds = wait
    if cookie_file:
        ctx.obj["cookie_file"] = cookie_file
    outcome = _run(ctx, "https://www.douyin.com/", plan_login)
    data = outcome.data
    if data.get("already_logged_in"):
        console.print("[green]✓ 该 profile 已处于登录态，无需重复登录。[/green]")
    elif data.get("ok"):
        console.print("[bold green]✓ 登录成功，登录态已保存到 profile。[/bold green]")
    else:
        console.print(f"[yellow]✗ {data.get('reason', '登录失败')}[/yellow]")
    _emit(ctx, data)
    if not data.get("ok"):
        raise typer.Exit(code=1)


@app.command("status")
def cmd_status(ctx: typer.Context) -> None:
    """查看当前 profile 的登录状态。"""
    from .actions import plan_status

    outcome = _run(ctx, "https://www.douyin.com/", plan_status)
    data = outcome.data
    if data.get("logged_in"):
        console.print(f"[green]✓ 已登录[/green]  profile={ctx.obj['profile'] or 'default'}")
    else:
        console.print(f"[yellow]✗ 未登录[/yellow]  profile={ctx.obj['profile'] or 'default'}")
        console.print("  执行 [bold]dy login[/bold] 扫码登录。")
    _emit(ctx, data)


@app.command("logout")
def cmd_logout(ctx: typer.Context) -> None:
    """清除本地登录态。"""
    import shutil

    settings = _settings(ctx)
    profile = ctx.obj["profile"] or settings.default_profile
    profile_dir = settings.profile_dir(profile)
    existed = profile_dir.exists()
    if existed:
        shutil.rmtree(profile_dir, ignore_errors=True)
    console.print(f"[green]✓ 已清除 profile 登录态:[/green] {profile_dir}")
    _emit(ctx, {"ok": True, "removed": existed, "profile": profile})


# ---------------------------------------------------------------- 发布


@app.command("publish")
def cmd_publish(
    ctx: typer.Context,
    video: str = typer.Argument(..., help="视频文件路径"),
    title: str = typer.Option("", "--title", "-t", help="作品标题/简介"),
    desc: str = typer.Option("", "--desc", "-d", help="补充描述"),
    topic: list[str] = typer.Option([], "--topic", help="话题，可重复；例如 --topic 咖啡"),
    cover: str | None = typer.Option(None, "--cover", help="自定义封面图片路径"),
    schedule: str | None = typer.Option(None, "--schedule", help="定时发布时间"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过发布前确认"),
) -> None:
    """发布一个视频到创作者中心。"""
    from pathlib import Path

    from .actions import plan_publish

    video_path = Path(video).expanduser()
    if not video_path.exists():
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    body_parts = [p for p in (title, desc) if p]
    body = "\n".join(body_parts)
    topics = " ".join(f"#{t.strip().lstrip('#')}" for t in (topic or []) if t.strip())
    if topics:
        body = f"{body}\n{topics}".strip() if body else topics

    console.print()
    console.rule("[bold]发布前确认")
    console.print(f"  视频   : {video_path}")
    console.print(f"  简介   : {body or '(空)'}")
    console.print(f"  话题   : {topics or '(无)'}")
    console.print(f"  封面   : {cover or '(默认)'}")
    console.print(f"  定时   : {schedule or '(立即)'}")

    if not yes:
        if not typer.confirm("确认发布？", default=False):
            console.print("[yellow]已取消发布。[/yellow]")
            _emit(ctx, {"ok": False, "cancelled": True, "published": False})
            raise typer.Exit(code=0)

    plan = functools.partial(
        plan_publish, video=str(video_path), body=body, cover=cover, schedule=schedule
    )
    outcome = _run(ctx, "https://creator.douyin.com/creator-micro/content/upload", plan)
    data = outcome.data
    data.setdefault("video", str(video_path))
    if data.get("published"):
        console.print("[bold green]✓ 发布成功[/bold green]")
    else:
        shot = _settings(ctx).artifact_path(f"publish-unknown-{int(time.time())}")
        data["screenshot"] = str(shot)
        console.print(f"[yellow]未能确认发布结果，请检查截图: {shot}[/yellow]")
    _emit(ctx, data)
    if not data.get("published"):
        raise typer.Exit(code=1)


# ---------------------------------------------------------------- 内容


@app.command("posts")
def cmd_posts(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", help="返回条数"),
    scrolls: int = typer.Option(3, "--scrolls", help="滚动翻页次数"),
) -> None:
    """列出自己账号的作品与数据。"""
    from .actions import extract_posts, interact_posts

    plan = functools.partial(interact_posts, scroll_rounds=scrolls)
    outcome = _run(
        ctx,
        "https://creator.douyin.com/creator-micro/content/manage",
        plan,
        extract=functools.partial(extract_posts, limit=limit),
    )
    posts = outcome.data.get("posts", [])
    if not (ctx.obj and ctx.obj.get("json")):
        _print_posts(posts)
    else:
        emit_json(posts)


@app.command("metrics")
def cmd_metrics(ctx: typer.Context) -> None:
    """抓取创作者中心数据概览。"""
    from .actions import extract_metrics, interact_metrics

    outcome = _run(
        ctx,
        "https://creator.douyin.com/creator-micro/data-center/content",
        interact_metrics,
        extract=extract_metrics,
    )
    data = outcome.data
    _emit(ctx, data)
    if not data.get("ok"):
        console.print("[yellow]未捕获到数据接口——可运行 dy probe 查看实际接口路径。[/yellow]")


# ---------------------------------------------------------------- 互动


@app.command("comments")
def cmd_comments(
    ctx: typer.Context,
    aweme_id: str = typer.Argument(..., help="作品 ID"),
    limit: int = typer.Option(20, "--limit", "-n", help="返回条数"),
) -> None:
    """抓取某个作品的评论。"""
    from .actions import extract_comments, interact_comments

    plan = functools.partial(interact_comments, aweme_id=aweme_id)
    outcome = _run(
        ctx,
        f"https://www.douyin.com/video/{aweme_id}",
        plan,
        extract=functools.partial(extract_comments, limit=limit),
    )
    comments = outcome.data.get("comments", [])
    if not (ctx.obj and ctx.obj.get("json")):
        _print_comments(comments)
    else:
        emit_json(comments)


@app.command("reply")
def cmd_reply(
    ctx: typer.Context,
    aweme_id: str = typer.Argument(..., help="作品 ID"),
    text: str = typer.Option(..., "--text", "-m", help="回复内容"),
    cid: str | None = typer.Option(None, "--cid", help="要回复的评论 cid；不给则发作品评论"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过发送前确认"),
) -> None:
    """在作品下发表评论或回复某条评论。"""
    from .actions import plan_reply

    console.print()
    console.rule("[bold]发送前确认")
    console.print(f"  作品   : {aweme_id}")
    console.print(f"  回复到 : {cid or '(作品评论区)'}")
    console.print(f"  内容   : {text}")
    if not yes:
        if not typer.confirm("确认发送？", default=False):
            console.print("[yellow]已取消发送。[/yellow]")
            _emit(ctx, {"ok": False, "cancelled": True, "sent": False})
            raise typer.Exit(code=0)

    plan = functools.partial(plan_reply, aweme_id=aweme_id, text=text, cid=cid)
    outcome = _run(ctx, f"https://www.douyin.com/video/{aweme_id}", plan)
    data = outcome.data
    if data.get("sent") and data.get("input_cleared"):
        console.print("[bold green]✓ 评论已发送[/bold green]")
    else:
        console.print("[yellow]发送结果无法确认，请检查页面状态。[/yellow]")
    _emit(ctx, data)
    if not data.get("sent"):
        raise typer.Exit(code=1)


# ---------------------------------------------------------------- 检索


@app.command("search")
def cmd_search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="搜索关键词"),
    kind: str = typer.Option("video", "--kind", "-k", help="video | user | all"),
    limit: int = typer.Option(20, "--limit", "-n", help="返回条数"),
) -> None:
    """搜索视频或用户。"""
    from urllib.parse import quote

    from .actions import extract_search, interact_search

    plan = functools.partial(interact_search, query=query, kind=kind)
    type_param = {"video": "video", "user": "user", "all": "general"}.get(kind, "video")
    url = f"https://www.douyin.com/search/{quote(query)}?type={type_param}"
    outcome = _run(ctx, url, plan, extract=functools.partial(extract_search, limit=limit))
    results = outcome.data.get("results", [])
    if not (ctx.obj and ctx.obj.get("json")):
        _print_results(results)
    else:
        emit_json(results)


@profile_app.command("show")
def cmd_profile_show(ctx: typer.Context) -> None:
    """查看当前登录账号的资料。"""
    from .actions import extract_profile, interact_profile

    outcome = _run(
        ctx, "https://www.douyin.com/", interact_profile, extract=extract_profile
    )
    data = outcome.data
    if not (ctx.obj and ctx.obj.get("json")):
        _print_profile(data)
    else:
        emit_json(data)


@profile_app.command("posts")
def cmd_profile_posts(
    ctx: typer.Context,
    sec_uid: str = typer.Argument(..., help="目标用户的 sec_uid"),
    limit: int = typer.Option(20, "--limit", "-n", help="返回条数"),
) -> None:
    """查看指定用户的作品列表。"""
    from .actions import extract_user_posts, interact_user_posts

    plan = functools.partial(interact_user_posts, sec_uid=sec_uid)
    outcome = _run(
        ctx,
        f"https://www.douyin.com/user/{sec_uid}",
        plan,
        extract=functools.partial(extract_user_posts, limit=limit),
    )
    posts = outcome.data.get("posts", [])
    if not (ctx.obj and ctx.obj.get("json")):
        _print_posts(posts)
    else:
        emit_json(posts)


# ---------------------------------------------------------------- 内容层


@app.command("digest")
def cmd_digest(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="要抓取的页面 URL"),
    wait_for: str | None = typer.Option(
        None, "--wait-for", help="抓取前等待的选择器，如 css:.video-info"
    ),
    full_page: bool = typer.Option(False, "--full-page", help="滚动整页以加载全部内容"),
    shot: bool = typer.Option(False, "--shot", help="同时截图"),
) -> None:
    """抓取页面并转成 LLM-ready 的 Markdown。"""
    from .digest import digest_page

    payload = digest_page(
        _settings(ctx),
        url,
        profile=ctx.obj["profile"],
        wait_for=wait_for,
        full_page=full_page,
        screenshot=shot,
    )
    if not (ctx.obj and ctx.obj.get("json")):
        from .digest import print_digest

        print_digest(payload)
    else:
        emit_json({k: v for k, v in payload.items() if k != "markdown"})


@app.command("collect")
def cmd_collect(
    ctx: typer.Context,
    search: str | None = typer.Option(None, "--search", help="按关键词搜索作品"),
    user: str | None = typer.Option(None, "--user", help="创作者的 sec_uid"),
    from_url: str | None = typer.Option(
        None, "--from-url", help="任意含作品链接的页面（如首页、话题页）"
    ),
    ids: str | None = typer.Option(None, "--ids", help="逗号分隔的 aweme_id"),
    limit: int = typer.Option(10, "--limit", "-n", help="最多抓多少个作品"),
    concurrency: int = typer.Option(3, "--concurrency", "-c", help="并发数"),
) -> None:
    """批量把作品页转成 LLM-ready 的 Markdown。"""
    from .digest import collect_markdown, save_markdown, video_urls

    settings = _settings(ctx)
    aweme_ids: list[str] = []

    if ids:
        aweme_ids = [i.strip() for i in ids.split(",") if i.strip()]
    elif user:
        from .actions import extract_user_posts, interact_user_posts

        plan = functools.partial(interact_user_posts, sec_uid=user)
        outcome = _run(
            ctx,
            f"https://www.douyin.com/user/{user}",
            plan,
            extract=functools.partial(extract_user_posts, limit=limit),
        )
        aweme_ids = [p["aweme_id"] for p in outcome.data.get("posts", [])]
    elif search:
        from .actions import extract_search, interact_search

        plan = functools.partial(interact_search, query=search, kind="video")
        outcome = _run(
            ctx,
            f"https://www.douyin.com/search/{search}?type=video",
            plan,
            extract=functools.partial(extract_search, limit=limit),
        )
        aweme_ids = [p["aweme_id"] for p in outcome.data.get("results", [])]
    elif from_url:
        from .actions import plan_resolve_video_ids

        plan = functools.partial(plan_resolve_video_ids, limit=limit, scroll_rounds=3)
        outcome = _run(ctx, from_url, plan)
        aweme_ids = outcome.data.get("ids", [])
    else:
        console.print("[red]✗ 请指定 --search / --user / --from-url / --ids 之一[/red]")
        raise typer.Exit(code=2)

    aweme_ids = aweme_ids[:limit]
    if not aweme_ids:
        console.print("[yellow]没有取到作品（可能未登录触发风控，或关键词无结果）[/yellow]")
        raise typer.Exit(code=1)

    targets = video_urls(aweme_ids)
    console.print(f"[cyan]准备抓取 {len(targets)} 个作品…[/cyan]")
    items = collect_markdown(
        settings, targets, profile=ctx.obj["profile"], concurrency=concurrency
    )

    saved = []
    for item in items:
        p = save_markdown(settings, item["url"], item["markdown"])
        saved.append({"url": item["url"], "chars": item["chars"], "path": str(p)})

    if not (ctx.obj and ctx.obj.get("json")):
        from .utils import make_table

        table = make_table("批量抓取结果", ["作品 ID", "字数", "文件"])
        for s in saved:
            table.add_row(s["url"].rsplit("/video/", 1)[-1][:20], str(s["chars"]), s["path"])
        console.print(table)
        console.print(f"[green]✓ 共 {len(saved)}/{len(targets)} 个作品已保存到 {settings.artifacts_dir}[/green]")
    else:
        emit_json(saved)


@app.command("analyze")
def cmd_analyze(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="要分析的页面 URL"),
    task: str = typer.Option(..., "--task", "-t", help="交给 LLM 的分析任务"),
) -> None:
    """抓取页面并交给 LLM 按指定任务分析（这就是 LLM 该干的活）。"""
    from .digest import analyze_page

    result = analyze_page(_settings(ctx), url, task, profile=ctx.obj["profile"])
    if ctx.obj and ctx.obj.get("json"):
        emit_json(result)
    else:
        console.print(f"[bold]来源:[/bold] {result['url']}  [dim]({result['provider']})[/dim]")
        console.rule("[bold]分析结果")
        console.print(result["answer"])


# ---------------------------------------------------------------- cookie


@cookies_app.command("import")
def cmd_cookies_import(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="cookie 文件路径（cookies.txt 或 JSON）"),
    check: bool = typer.Option(True, "--check/--no-check", help="导入后校验是否已登录"),
) -> None:
    """从 cookie 文件导入抖音登录态（免扫码）。"""
    from .cookies import describe, filter_douyin, parse_cookie_file
    from .cookies import export_cookies as _export  # noqa: F401

    parsed = parse_cookie_file(path)
    kept, dropped = filter_douyin(parsed)
    console.print(f"[cyan]{path}[/cyan]")
    console.print(f"  解析结果: {describe(kept)}")
    if dropped:
        console.print(f"  已忽略 {dropped} 个非抖音 cookie")

    if not kept:
        console.print("[red]✗ 没有抖音域名的 cookie[/red]")
        raise typer.Exit(code=1)

    ctx.obj["cookie_file"] = path
    if check:
        from .actions import plan_status

        outcome = _run(ctx, "https://www.douyin.com/", plan_status)
        data = outcome.data
        if data.get("logged_in"):
            console.print("[bold green]✓ 导入成功，已登录[/bold green]（已存入本地 profile，之后不用再导）")
        else:
            console.print("[yellow]✗ cookie 注入后仍未登录——文件可能已过期[/yellow]")
            _emit(ctx, data)
            raise typer.Exit(code=1)
    _emit(ctx, {"ok": True, "count": len(kept), "dropped": dropped})


@cookies_app.command("export")
def cmd_cookies_export(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="导出目标路径（cookies.txt）"),
) -> None:
    """把当前登录态导出成 cookies.txt，方便备份或换机器。"""
    from .cookies import export_cookies

    captured: dict = {}

    async def plan(ctx_inner):
        captured["cookies"] = await ctx_inner.all_cookies()
        return {"ok": True}

    _run(ctx, "https://www.douyin.com/", plan)
    cookies = captured.get("cookies") or []
    if not cookies:
        console.print("[yellow]没有可导出的 cookie（可能未登录）[/yellow]")
        raise typer.Exit(code=1)
    out = export_cookies(cookies, path)
    console.print(f"[green]✓ 已导出 {len(cookies)} 个 cookie:[/green] {out}")


# ---------------------------------------------------------------- 排障


@app.command("doctor")
def cmd_doctor(ctx: typer.Context) -> None:
    """环境自检：依赖、浏览器、目录、profile。"""
    from .commands_debug import doctor

    result = doctor(_settings(ctx))
    _emit(ctx, result)
    if not result.get("ok"):
        raise typer.Exit(code=1)


@app.command("probe")
def cmd_probe(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="要检查的页面 URL"),
    no_shot: bool = typer.Option(False, "--no-shot", help="不保存截图"),
    wait: int = typer.Option(6000, "--wait", help="页面加载后等待毫秒数"),
) -> None:
    """dump 页面结构：data-e2e / 输入框 / 按钮 / 实际接口，用于维护选择器。"""
    from .commands_debug import probe

    result = probe(_settings(ctx), url, profile=ctx.obj["profile"], shot=not no_shot, wait_ms=wait)
    _emit(ctx, result)


@app.command("shot")
def cmd_shot(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="要截图的页面 URL"),
    out: str | None = typer.Option(None, "--out", "-o", help="输出图片路径"),
    full_page: bool = typer.Option(False, "--full-page", help="整页截图"),
) -> None:
    """给页面截个图。"""
    from .commands_debug import shot

    result = shot(
        _settings(ctx), url, out=out, profile=ctx.obj["profile"], full_page=full_page
    )
    _emit(ctx, result)


@app.command("version")
def cmd_version() -> None:
    """显示版本。"""
    console.print(f"dyctl {__version__}")


# ---------------------------------------------------------------- 输出辅助


def _print_posts(posts: list[dict]) -> None:
    from .utils import human_size, make_table

    table = make_table("作品列表", ["作品 ID", "标题", "发布时间", "播放", "点赞", "评论", "分享"])
    for p in posts:
        s = p.get("stats", {})
        table.add_row(
            p.get("aweme_id", ""),
            (p.get("title") or "-")[:28],
            p.get("create_time_str", "-"),
            human_size(s.get("play")),
            human_size(s.get("like")),
            human_size(s.get("comment")),
            human_size(s.get("share")),
        )
    console.print(table)


def _print_comments(comments: list[dict]) -> None:
    from .utils import make_table

    table = make_table("评论列表", ["cid", "用户", "内容", "点赞", "回复数", "时间"])
    for c in comments:
        table.add_row(
            c.get("cid", ""),
            c.get("author") or "-",
            (c.get("text") or "-")[:40],
            str(c.get("like", 0)),
            str(c.get("reply_count", 0)),
            c.get("create_time_str", "-"),
        )
    console.print(table)


def _print_results(results: list[dict]) -> None:
    from .utils import make_table

    table = make_table("搜索结果", ["作品 ID", "标题", "作者", "播放", "点赞"])
    for r in results:
        s = r.get("stats", {})
        table.add_row(
            r.get("aweme_id", ""),
            (r.get("title") or "-")[:36],
            r.get("author") or "-",
            str(s.get("play", 0)),
            str(s.get("like", 0)),
        )
    console.print(table)


def _print_profile(data: dict) -> None:
    from .utils import make_table

    if not data.get("ok"):
        console.print(f"[yellow]{data.get('reason', '未获取到资料')}[/yellow]")
        return
    table = make_table("账号资料", ["字段", "值"])
    for k, v in data.get("profile", {}).items():
        table.add_row(k, str(v) if v != "" else "-")
    console.print(table)


# ---------------------------------------------------------------- 入口


def run() -> None:
    """CLI 入口。

    把已知异常翻译成可读提示——自动化的报错信息本身就是排障信息，
    堆栈对使用者没有价值，对开发者才有用。
    """
    from .crawler import BlockedError

    try:
        app()
    except BlockedError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        sys.exit(1)
    except PermissionError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        sys.exit(1)
    except FileNotFoundError as exc:
        console.print(f"[red]✗ 文件不存在: {exc}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]已中断。[/yellow]")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗ {type(exc).__name__}: {exc}[/red]")
        console.print("[dim]加 -v 查看详细日志；用 `dy probe <url>` 排查选择器是否失效。[/dim]")
        if "--verbose" in sys.argv or "-v" in sys.argv:
            console.print_exception()
        sys.exit(1)


if __name__ == "__main__":
    run()
