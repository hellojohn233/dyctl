"""选择器集中注册表。

**这是全项目唯一需要随抖音改版而修改的文件。**

设计原则：
1. 每个目标给一串候选选择器，按可靠性从高到低排列。抖音的 `data-e2e` 属性
   比 class 名可靠（class 是哈希化的，data-e2e 是产品同学维护的）。
2. 全部使用 Playwright 的 CSS 引擎语法；`:has-text()` 这类 Playwright 伪类
   仅在明确标注处使用。
3. 优先用属性选择器（`[data-e2e=...]`）和语义化占位符，避免依赖布局层级。

排障方法：
    dy probe <url>          # 打开页面并 dump 候选元素
    dy probe <url> --shot   # 同时保存截图
然后把失效的选择器替换掉即可。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 作品链接

# 实测（2026-10）：抖音的作品链接挂在 <div href="//www.douyin.com/video/xxx"> 上，
# **不是 <a href>**。用 a[href*='/video/'] 一条都匹配不到，必须用不带标签限定的写法。
VIDEO_LINK = "[href*='/video/']"
VIDEO_ID_PATTERN = "/video/(\\d+)"

# ---------------------------------------------------------------- 风控拦截

BLOCK = {
    "captcha": [
        "#captcha_container",
        "[id*='captcha']",
        "div[class*='captcha']",
        "iframe[src*='captcha']",
    ],
    "login_wall": [
        "[data-e2e='login-panel']",
        "div[class*='login-panel']",
    ],
}

# ---------------------------------------------------------------- 登录

LOGIN = {
    "qrcode": [
        "[data-e2e='qrcode-img']",
        "img[class*='qrcode']",
        "canvas[class*='qrcode']",
        "//div[contains(@class,'qrcode')]//img",
    ],
    "login_panel": [
        "[data-e2e='login-panel']",
        "div[class*='login-panel']",
        "div[class*='loginContainer']",
    ],
    "user_avatar": [
        "[data-e2e='user-avatar']",
        "img[class*='avatar']",
        "div[class*='avatar']",
    ],
    "confirm": [
        "button:has-text('确认')",
        "button:has-text('确定')",
    ],
}

# ---------------------------------------------------------------- 发布 / 上传

PUBLISH = {
    "file_input": [
        "input[type='file']",
    ],
    "title_input": [
        "[data-e2e='video-desc']",
        "div[contenteditable='true'][data-line-wrapper]",
        "div[contenteditable='true']",
        "textarea[placeholder*='简介']",
        "textarea[placeholder*='标题']",
    ],
    "title_placeholder": [
        "填写作品简介",
        "填写作品标题",
        "作品简介",
    ],
    "publish_button": [
        "button:has-text('发布')",
        "[data-e2e='publish']",
        "div[class*='publishBtn']",
        "span[class*='publish']",
    ],
    "schedule_tab": [
        "text=定时发布",
        "[data-e2e='schedule-publish']",
    ],
    "topic_input": [
        "[data-e2e='topic-input']",
        "input[placeholder*='话题']",
        "input[placeholder*='搜索话题']",
    ],
    "cover_add": [
        "[data-e2e='cover-add']",
        "text=上传封面",
        "text=设置封面",
    ],
    "publish_success": [
        "[data-e2e='publish-success']",
        "text=发布成功",
        "text=作品发布成功",
    ],
}

# ---------------------------------------------------------------- 内容管理

CONTENT = {
    "publish_tab": [
        "text=作品管理",
        "[data-e2e='post-manage']",
    ],
    "post_item": [
        "[data-e2e='post-item']",
        "tr[class*='table-row']",
        "div[class*='video-item']",
    ],
    "data_tab": [
        "text=数据概览",
        "text=数据分析",
        "[data-e2e='data-center']",
    ],
}

# ---------------------------------------------------------------- 评论

COMMENT = {
    "input": [
        "[data-e2e='comment-input']",
        "div[contenteditable='true'][data-placeholder*='评论']",
        "div[contenteditable='true']",
        "textarea[placeholder*='评论']",
        "input[placeholder*='评论']",
    ],
    "input_placeholder": [
        "留下你的精彩评论吧",
        "友善的评论是交流的开始",
        "说点什么...",
    ],
    "send_button": [
        "[data-e2e='comment-send']",
        "button:has-text('发送')",
        "span:has-text('发送')",
    ],
    "reply_button": [
        "[data-e2e='comment-reply']",
        "span:has-text('回复')",
    ],
    "item": [
        "[data-e2e='comment-item']",
        "div[class*='comment-item']",
        "div[class*='commentItem']",
    ],
}

# ---------------------------------------------------------------- 搜索

SEARCH = {
    "input": [
        "input[placeholder*='搜索']",
        "[data-e2e='search-input']",
        "input[class*='search']",
    ],
    "video_tab": [
        "text=视频",
        "[data-e2e='search-video-tab']",
    ],
    "user_tab": [
        "text=用户",
        "[data-e2e='search-user-tab']",
    ],
    "result_item": [
        "[data-e2e='search-result-item']",
        "div[class*='search-result']",
    ],
}
