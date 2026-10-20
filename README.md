# dyctl

> 抖音网页端自动化 CLI。命令:`dyctl`

抖音**网页端**自动化 CLI。基于 **Crawl4AI**（它本身跑在 Playwright 上）驱动真实浏览器，
完成登录、发布、数据抓取、评论互动，以及把页面转成 LLM 能直接消化的 Markdown。

> 面向内容运营 / 批量发布的工程化工具，不是爬虫玩具。
> 数据抓取复用页面自身发出的合法请求，不做签名逆向。

---

## 选型：为什么全栈用 Crawl4AI

**结论：全栈 Crawl4AI。** 它本身基于 Playwright，所以这不是"二选一"，
而是"要不要自己重新造一遍轮子"。

### Crawl4AI 到底能不能做写操作？能

C4A-Script 的 40 个指令里确实没有文件上传（`GO / WAIT / CLICK / TYPE / PRESS /
SCROLL / SET / IF / REPEAT / EVAL / PROCED / INCLUDE`…），但**钩子机制把原始
Playwright `page` 给了你**：

```python
async def on_execution_started(page, context, config, **kwargs):
    await page.set_input_files("input[type=file]", video)   # 完全可用
    await page.fill("div[contenteditable=true]", "作品简介")
    await page.click("button:has-text('发布')")
```

实测（`tests/verify_crawl4ai_write.py`，**6/6 通过**）：在表单页上填字段 + 点提交，
服务端收到了 POST（`post_data` 含 `custname=%E4%B8%80%E6%8B%B3&custtel=13800138000`），
返回值回显正确。**写操作完全成立。**

> 🔑 **坑**：`on_execution_started` 在源码里是**嵌套在 `if config.js_code:` 内部**的
> （`async_crawler_strategy.py:1050`）。不设 `js_code`，钩子**根本不会触发**。
> 这就是为什么很多人以为"钩子没用"。本项目在引擎层统一注入了触发器。

### Crawl4AI 能抓抖音那种带签名的接口响应体吗？能

抖音 creator 站与 www 站的数据接口都带 `a_bogus` / `X-Bogus` 签名，
签名由前端 JS 生成 —— 自己构造请求必然失败，且逆向签名算法会随前端更新而失效。

但**页面自己发的请求一定是合法的**：前端会算好签名再发。

Crawl4AI 的 `capture_network_requests` 在导航**之前**就挂了监听，
覆盖整个页面加载周期，而且 0.9.5 的 response 事件**带 body**：

```python
"body": { "text": text_body }   # = await response.text()
```

实测 `json.loads(body["text"])` 能直接解析出抖音接口的 JSON。
（官方文档「Network & Console Capture」页展示的是**旧结构**、没有 body ——
**文档没跟上实现**。我一开始就是被文档带偏的，这也是本项目被推翻重写的原因。）

### 那全栈用它的代价是什么？

坦白说：**写操作那部分代码，你写的仍然是 Playwright**，只是外面套了一层。
所以要清醒地知道 Crawl4AI 到底带来了什么：

| 能力 | 自己做要多久 | Crawl4AI 给什么 |
|---|---|---|
| 浏览器生命周期 / 持久化 profile | 2~3 天 | `use_persistent_context` + `user_data_dir` |
| 反自动化指纹 | 不断踩坑 | `enable_stealth` + `simulate_user` + `override_navigator` |
| Markdown 抽取（LLM-ready） | 好几天，且做不好 | `DefaultMarkdownGenerator` + 内容过滤 |
| 批量并发调度 | 自己写调度器 | `arun_many` + 内存自适应 dispatcher |
| 深度爬取 / URL 发现 | 很难写 | `DeepCrawlStrategy` / `AsyncUrlSeeder` / `DomainMapper` |
| 常驻服务形态 | 自己搭 | Docker server + REST API + **MCP** |
| 代理轮换 / 反爬重试 | 复杂 | `proxy_config` 升级链 + `max_retries` + `fallback_fetch_function` |

**换句话说：写操作那点 Playwright 代码，省下来的是上面这一整张表。**

### 那还剩什么值得自己写的？

只有三样，因为它们是抖音特有的：

1. **选择器注册表**（`selectors.py`）—— 抖音的 DOM 是它自己的，不是通用爬虫问题
2. **接口路径常量**（`crawler.py` 的 `API` 字典）—— 抖音的接口路径是它自己的
3. **风控识别**（验证码中间页）—— Crawl4AI 的反检测只覆盖 Cloudflare/Akamai/
   PerimeterX/DataDome/Imperva，**不认识抖音的验证码页**，
   不自己加检测的话，它会静默返回一份几乎空的 Markdown，看起来像"抓到了但没内容"

### 顺带说一句：LLM 该放在哪一层

**LLM 不该驱动浏览器，但非常适合处理内容。**

| 环节 | 用什么 | 为什么 |
|---|---|---|
| 点按钮、填表单、等上传、翻页 | Crawl4AI 钩子里的原始 page | 确定性与可审计 |
| 风控对抗、指纹、会话保持 | Crawl4AI BrowserConfig（+ 本项目补的 UA 修正） | 需要底层控制权 |
| 抓页面正文 → Markdown | Crawl4AI | 它的主场 |
| 批量爬一个创作者的全部作品 | Crawl4AI `arun_many` | 开箱即用 |
| 起标题、选题分析、竞品钩子拆解 | LLM | 需要语义创造力 |
| 评论语义分类、识别负面评论 | LLM | 需要语义理解 |

所以 `dyctl analyze` 是这个项目里 LLM 唯一出场的地方——它不碰浏览器，只处理已经抓好的内容。

### 那 Playwright MCP 呢？

MCP（Model Context Protocol）解决的是**另一个问题**，别混：

| | 本项目这种 CLI | Playwright MCP |
|---|---|---|
| 谁在决定点哪里 | **你的代码**（确定性的命令链） | **LLM 现场看屏幕决定** |
| 形态 | 一条命令跑完，结果可复现 | LLM 通过工具调用一步步探索 |
| 适合 | 批量运营、发布流水线、数据入库 | 让 AI 自己去看网页、做一次性调研 |
| 失败时 | 异常栈 + 明确报错 | LLM 可能"觉得"成功了 |

**一句话：MCP 是把浏览器操作权交给 AI；CLI 是把浏览器操作权留在代码里。**
你这个项目要做的是「一键发布」「批量抓取」「定时跑」——
这些都需要确定性，所以走 CLI。

想两头都要也行：CLI 保持确定性产出数据，再让 LLM 读这些数据做分析
（就是 `dyctl analyze` 干的事）。**这比上 MCP 更省 token、更可审计、更好调试。**
真要上 MCP，Crawl4AI 自带 MCP server（Docker 起一个），本项目可以只读它。

---

## 架构

全栈跑在 **Crawl4AI** 上（它本身基于 Playwright）。三层：

```
src/dyctl/
├── cli.py            Typer 命令入口（同步 → asyncio.run）
├── crawler.py        ★ 引擎：BrowserConfig / RunConfig / 会话执行 / 响应捕获
├── actions.py        ★ 动作层：每个命令一个 plan + 一个 extract
├── digest.py         ★ 内容层：抓取 → Markdown → LLM 分析
├── commands_debug.py doctor / probe / shot
├── api.py            JSON 取值工具（防御式）
├── selectors.py      ★ 选择器集中注册表
├── models.py         数据归一化（Post / Comment / UserProfile）
├── config.py         配置与本地路径
└── utils.py          日志 / 表格 / 格式化
```

### ★ crawler.py：引擎

三件关键设计：

**① 动作层用钩子，而不是自己管 Playwright**

Crawl4AI 的 `arun` 生命周期是「导航 → 抽取 → 返回」，而抖音的写操作
（上传、填简介、点发布）需要页面级动作。解法是 `on_execution_started` 钩子——
它交给你的就是**原始 Playwright page**：

```python
async def plan(ctx):
    await ctx.page.set_input_files("input[type=file]", video)   # 完全可用
    await ctx.page.fill("div[contenteditable=true]", "作品简介")
    await ctx.page.click("button:has-text('发布')")
    return {"_capture": [...]}      # 声明要抓哪些接口
```

> 🔑 **三个实测踩过的坑**：
> 1. `on_execution_started` 嵌套在 `if config.js_code:` 内部
>    （`async_crawler_strategy.py:1050`），**不设 `js_code` 钩子根本不触发**。
>    引擎统一注入了 `js_code="void 0;"` 兜底。
> 2. 钩子里 `raise` 会被 Crawl4AI 的 `_crawl_web` 包成通用 `RuntimeError`
>    （还附一大段源码上下文），错误类型就丢了。引擎改成在钩子里**捕获后不抛**，
>    等 `arun` 返回后再抛，CLI 才能给出干净提示。
> 3. `arun` 一返回，捕获监听就被摘掉。抖音的业务接口是首屏渲染后才陆续发出的，
>    所以没有 plan 时引擎默认 `settle_ms=3000` 等一等——不等就整个错过。

**② 数据层用 Crawl4AI 的网络捕获，不是自己发请求**

抖音 creator 站与 www 站的数据接口都带 `a_bogus` / `X-Bogus` 签名，
签名由前端 JS 生成 —— 自己构造请求必然失败，且逆向签名算法会随前端更新而失效。

但**页面自己发的请求一定是合法的**：前端会算好签名再发。

Crawl4AI 的 `capture_network_requests` 在导航**之前**就挂了监听，
覆盖整个页面加载周期，而且 0.9.5 的 response 事件**带 body**：

```python
"body": { "text": text_body }   # = await response.text()
```

（官方文档「Network & Console Capture」页展示的是旧结构，没有 body——
**文档没跟上实现**。这点我一开始就被带偏了。）

所以引擎从 `result.network_requests` 里按路径筛出响应体并解析：

```python
def json_bodies(result, pattern) -> list[dict]:
    for e in result.network_requests:
        if e.get("event_type") == "response" and pattern in e["url"]:
            yield json.loads(e["body"]["text"])
```

> ⚠️ 为什么不用 `expect_response`：钩子在导航**之后**才触发，那时首屏接口
> 早就发完了，而 `expect_response` 只能等待**未来**发生的响应。
> `expect_response` 留给"由我的动作触发的请求"（比如点"加载更多"）。

**③ 指纹层自己兜底**

Crawl4AI 的 BrowserConfig 默认 UA 写死为
`"Mozilla/5.0 (X11; Linux x86_64) ... Chrome/116.0.0.0"`，
而 `sec-ch-ua` 是**从这个 UA 推导**的。

在 macOS 上跑真实 Chrome 155 却对外发送 Linux UA，
UA / 操作系统 / Client Hints / 真实版本四方矛盾——
这类自相矛盾的特征比无头模式更隐蔽，也更容易被风控抓到。

引擎读取本机浏览器真实版本号重建 UA，Client Hints 会跟着变成同一版本：

```
User-Agent     : Mozilla/5.0 (Macintosh; ...) Chrome/155.0.8059.85 Safari/537.36
Sec-Ch-Ua      : "Chromium";v="155", "Not_A Brand";v="8", "Google Chrome";v="155"
Sec-Ch-Ua-Platform : "macOS"
```

### ★ selectors.py：唯一需要随改版维护的文件

所有选择器集中在一处，每个目标给一串候选，按可靠性排序：
`data-e2e` 属性 > 语义化占位符 > class 名。

改版后的修复流程：

```bash
dyctl probe https://creator.douyin.com/creator-micro/content/upload
```

这条命令会 dump 出页面上真实的 `data-e2e` 值、可见按钮、输入框占位符、
以及实际调用的接口路径 —— 照着改 `selectors.py` 即可，不用猜。

---

## 安装

```bash
cd dyctl
uv sync                     # crawl4ai + playwright + typer + rich
uv run playwright install chromium
```

需要 Python ≥ 3.11。

> macOS 上如果装了 Google Chrome，会自动优先使用系统 Chrome
> （与日常环境一致，比内置 Chromium 更不容易被风控识别）。
> 引擎会读取本机 Chrome 的真实版本号来重建 UA，保证与 Client Hints 自洽。

## 使用

所有命令支持 `--help`，全局选项有 `--profile` / `--headless` / `--slow-mo` / `--verbose` / `--json`。

```bash
# 1. 登录（弹出浏览器，扫码即可）
dyctl login

# 2. 确认状态
dyctl status

# 3. 发布
dyctl publish ~/Videos/clip.mp4 --title "手冲咖啡的三个关键" --topic 咖啡 --topic 手冲

# 4. 看自己的作品数据
dyctl posts --limit 20

# 5. 数据概览
dyctl metrics

# 6. 抓评论 / 回复评论
dyctl comments 7123456789012345678
dyctl reply 7123456789012345678 --text "感谢分享！" --cid 7300000000000000000

# 7. 搜索
dyctl search "露营装备" --kind video

# 8. 我的账号资料
dyctl profile show

# 9. 排障
dyctl doctor
dyctl probe https://www.douyin.com/
```

### 内容层

```bash
# 10. 页面 → LLM-ready Markdown（复用同一份登录态）
dyctl digest https://www.douyin.com/video/7123456789012345678

# 11. 批量抓一个创作者主页下的所有作品
dyctl collect https://www.douyin.com/user/MS4wLjABAAAAxxxx -n 20 -c 3

# 12. 抓完直接交给 LLM 按任务分析
export DY_LLM_PROVIDER=deepseek/deepseek-chat
export DY_LLM_API_KEY=sk-xxxx
dyctl analyze https://www.douyin.com/user/MS4wLjABAAAAxxxx --task "总结这个账号的选题方向和爆款共性"
```

LLM 配置走环境变量，LiteLLM 会自动识别各厂商：

| 变量 | 说明 | 默认 |
|---|---|---|
| `DY_LLM_PROVIDER` | LiteLLM provider，如 `openai/gpt-4o-mini`、`deepseek/deepseek-chat`、`anthropic/claude-3-5-sonnet-20240620` | `openai/gpt-4o-mini` |
| `DY_LLM_API_KEY` | API Key（不设则用厂商自己的环境变量，如 `OPENAI_API_KEY`） | — |
| `DY_LLM_BASE_URL` | 自定义端点 | — |
| `DY_LLM_MODEL` | 覆盖模型名 | 同 provider |

### Cookie 导入导出

不想扫码?从浏览器插件导出 cookie 文件喂进来就行。

```bash
# 导入(自动识别格式,导入后已存进本地 profile,以后不用再导)
dyctl cookies import ~/Downloads/cookies.txt

# 临时用一次
dyctl --cookie-file ~/Downloads/cookies.txt posts

# 备份当前登录态(换机器/换 profile 用)
dyctl cookies export ~/backup-cookies.txt
```

**支持 4 种格式,自动识别:**

| 格式 | 长相 | 来源 |
|---|---|---|
| Netscape cookies.txt | `.douyin.com\tTRUE\t/\tFALSE\t1793696400\tsessionid\tabc` | 插件 "Get cookies.txt LOCALLY" / EditThisCookie / Cookie-Editor |
| JSON 数组 | `[{"domain":".douyin.com","name":"sessionid","value":"...","expirationDate":...}]` | Cookie-Editor / EditThisCookie 导出 JSON |
| JSON 对象 | `{"sessionid":"...","sid_tt":"..."}` | 手写 / 其它工具 |
| Playwright storage_state | `{"cookies":[...],"origins":[...]}` | 本工具 `dyctl cookies export` 之前存的 |

**怎么拿 cookie 文件:**

1. Chrome 装插件 **Get cookies.txt LOCALLY** 或 **EditThisCookie**
2. 打开 `douyin.com`,确认已登录
3. 插件里导出这个域名的 cookie
4. `dyctl cookies import <文件>`

> ⚠️ 只会保留 `douyin.com` 域名的 cookie,其它站点的自动丢掉。
> 注入用的是 Crawl4AI 的 `context.add_cookies()`,对持久化 profile 有效 ——
> **导入一次就写进 profile 的 cookie 库,之后所有命令都不用再导。**
> cookie 过期了就重新导一次。

### 多账号

不同 profile 各自持有独立的 cookie 与登录态：

```bash
dyctl --profile brand-a login
dyctl --profile brand-b login
dyctl --profile brand-a posts
```

### JSON 输出

```bash
dyctl posts --json | jq '.[] | {id: .aweme_id, play: .stats.play}'
```

---

## 安全设计

- **发布和发评论前一定会打印摘要并要求确认**，`--yes` 可跳过。
- 非交互环境（管道、CI）中，Typer 的确认会自动中止，不会误发。
- 写操作后会校验结果（输入框是否清空、URL 是否跳转），
  无法确认时保存现场截图到 `~/.dyctl/artifacts/`。

## 本地状态

```
~/.dyctl/
├── profiles/<name>/   持久化浏览器 profile（cookie / 指纹）
├── artifacts/         截图与失败现场
└── downloads/         下载内容
```

`dyctl logout` 会删除对应 profile。

---

## 实测结论（2026-10 验证）

这几条是真正上手时必然会撞上的，先说清楚：

| 结论 | 影响 |
|---|---|
| **必须先 `dyctl login`**。未登录状态下访问 `www.douyin.com/search/...` 会被重定向到**验证码中间页**，页面里一条业务接口都不发 | 所有依赖搜索/详情/评论的命令都必须先登录。代码里已内置检测，命中时直接报"请先执行 `dyctl login`"，而不是干等接口超时 |
| 公开页（首页、热搜榜）不登录也能访问 | `dyctl probe`、`dyctl digest https://www.douyin.com/` 可用于无登录环境，实测能拿到推荐流的标题/作者/播放量/封面 |
| **作品详情页未登录时只渲染导航骨架**（正文是"视频数据加载中"） | `dyctl digest` 抓单个作品页需要登录态；抓首页推荐流不需要 |
| **作品链接挂在 `<div href="//www.douyin.com/video/xxx">` 上，不是 `<a href>`** | `a[href*='/video/']` 在抖音上匹配 **0 条**，必须用 `[href*='/video/']`（见 `selectors.VIDEO_LINK`）。这个坑不踩会一直以为"页面上没有作品链接" |
| 抖音首页推荐流是 **SSR 进 HTML** 的，不走 XHR | 所以首页要取作品 ID 只能从 DOM/HTML 里解析，不能指望拦截接口 |
| 无头模式下 Chromium 的 UA 写死 `HeadlessChrome/xxx` | 已在 `crawler.py` 自动修正为 `Chrome/xxx`，且**只改标识符不动版本号**——`Sec-Ch-Ua` 本来就不含 HeadlessChrome，改完与 Client Hints 依然自洽 |
| **Crawl4AI 的默认 UA 写死为 Linux Chrome 116**，而 `sec-ch-ua` 由它推导 | 在 macOS 上跑真实 Chrome 155 却发 Linux UA，UA / 系统 / Client Hints 三方矛盾。已在 `crawler.py` 读取本机 Chrome 真实版本号重建 UA，实测请求头为 `Chrome/155.0.8059.85` + `sec-ch-ua v="155"`，完全自洽 |
| 抖音的 class 名是哈希化的且每周都变 | 所以数据抓取走 JSON 接口而非 DOM 解析；选择器集中在一个文件 |
| 创作者中心接口需要登录态 | `dyctl posts` / `dyctl metrics` / `dyctl publish` 都在登录后才能用 |
| **`on_execution_started` 钩子不设 `js_code` 就不触发** | 引擎统一注入 `js_code="void 0;"` 兜底。踩过这个坑才会明白为什么"钩子没用" |
| **钩子里 `raise` 会被 Crawl4AI 包成通用 `RuntimeError`**（还附一大段源码上下文） | 引擎改成在钩子里捕获后**不抛**，等 `arun` 返回再抛，CLI 才能给出干净提示 |
| **`arun` 一返回，捕获监听就被摘掉** | 抖音的业务接口是首屏渲染后才陆续发出的。引擎默认 `settle_ms=3000` 等一等,不等就整个错过（实测：等 0ms 抓到 0 个接口，等 3s 抓到 14 个） |
| **Crawl4AI 0.9.5 的 `capture_network_requests` 带 body**（`body.text`） | 官方文档页展示的是旧结构没有 body,**文档没跟上实现**。这是本项目被推翻重写的直接原因 |
| Crawl4AI 的反检测不认识抖音的验证码页 | 它只覆盖 Cloudflare/Akamai/PerimeterX/DataDome/Imperva。不自己加检测的话会静默返回近乎空的 Markdown，看着像"抓到了但没内容" |

已实测确认的公开接口路径（记录在 `crawler.py` 的 `API` 字典里）：

```
/aweme/v1/web/hot/search/list/          首页热搜榜
/aweme/v1/web/query/user/               用户查询
/aweme/v1/web/social/count              社交计数
/aweme/v1/web/get/user/risklevel/       风控等级
/aweme/v1/web/get/user/settings/        用户设置
/aweme/v1/web/solution/resource/list/    资源位
/aweme/v1/web/external/notification/    通知
/aweme/v1/web/home/channel/setting/     首页频道配置
/aweme/v1/web/douyin/select/tab/course/catagory/tag/  课程分类
/aweme/v2/web/module/feed/              推荐流（v2）
```

标注 `[待校准]` 的路径需要登录态才能触达，首次登录后用 `dyctl probe` 核对一次即可。

## 首次使用流程

```bash
cd dyctl
uv sync
uv run playwright install chromium   # 若本机已有缓存可跳过

uv run dyctl doctor          # 1. 环境自检
uv run dyctl login           # 2. 扫码登录（浏览器会弹出来）
uv run dyctl status          # 3. 确认已登录
uv run dyctl posts           # 4. 试试能不能拉到自己的作品
```

登录态存在 `~/.dyctl/profiles/default/`，
抖音的扫码是**账号级**的——同一个微信号/抖音号在浏览器里只扫一次，后续复用 profile 即可。

## 自检

```bash
uv run python tests/smoke_test.py
```

覆盖：模型归一化、浏览器启动、反检测注入（`navigator.webdriver` 抹除）、
UA 与 Client Hints 一致性、JSON 拦截器、抖音站点可达性、
内容层 UA 一致性与 Markdown 生成、作品链接选择器。
不需要登录态，当前 **33/33 通过**。

```bash
uv run python tests/verify_crawl4ai_write.py
```

单独验证"Crawl4AI 能不能做写操作"：用 `on_execution_started` 钩子拿到原始
Playwright `page`，在表单页上填字段 + 提交，确认服务端收到 POST。
当前 **6/6 通过**。这个脚本是"选型结论"的证据，改架构前建议先跑一遍。

---

## 已知限制

- **选择器需要实测校准。** 抖音的 DOM 结构随版本变化，
  首次使用某个页面时建议先跑 `dyctl probe` 核对 `selectors.py`。
  这类"把页面结构固化成选择器"的工作无法完全避免，
  但本项目已把它收敛到单一文件 + 一条诊断命令。
- **接口路径（`crawler.py` 中的 `API` 字典）同样可能随版本变化**，
  `dyctl probe` 会列出实际调用的接口路径，便于更新。
- 无头模式极易被风控识别，**默认有头**，`--headless` 仅供调试。
- 发布与评论的按钮定位依赖 DOM（数据抓取才走接口），
  抖音改版时这一层需要更新 `selectors.py`。
- 自动化操作可能违反抖音用户协议，请自行评估合规风险并控制频率。
