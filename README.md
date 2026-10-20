# dyctl

抖音网页端自动化 CLI。命令行里操作抖音网页：登录、发作品、看数据、回评论、把页面存成 Markdown 交给 AI 读。

底层是 Crawl4AI（它跑在 Playwright 上），驱动的是真实 Chrome，行为跟你在浏览器里手动操作一致。

## 适合做的事

- 定时发作品，不用守着点上传
- 把自己的作品数据和评论拉下来存库
- 批量把某个账号的作品页转成 Markdown，喂给 AI 做选题拆解
- 盯评论区，挑出要回的那几条再回

抓的是页面自己请求到的数据，不会去逆向抖音的接口签名。抖音接口改版时，顶多某条命令暂时不准。

## 安装

需要 Python ≥ 3.11。本机装了 Google Chrome 会被自动用上，跟日常环境一致，比内置 Chromium 更不容易被风控盯上。

```bash
cd dyctl
uv sync
uv run playwright install chromium
```

装完先自检：

```bash
uv run dyctl doctor
```

## 第一次用

```bash
uv run dyctl login      # 弹出浏览器，用抖音 App 扫码
uv run dyctl status     # 确认登录上了
uv run dyctl posts      # 拉一次自己的作品，看看数据通不通
```

登录态存在 `~/.dyctl/profiles/default/`。同一个账号扫一次就够，后面所有命令复用这份状态。

## 命令和参数

命令列表、每个命令的全部参数、默认值，都直接问 `--help`：

```bash
dyctl --help              # 全部命令 + 全局选项
dyctl publish --help      # 某个命令的参数
dyctl cookies import --help
```

下面只列几个组合好的用法，直接抄。

## 常用命令

### 发布

```bash
dyctl publish ~/Videos/clip.mp4 \
  --title "手冲咖啡的三个关键" \
  --desc "水温、粉量、闷蒸时间" \
  --topic 咖啡 --topic 手冲
```

可选：`--cover <图片>` 换封面，`--schedule "<时间>"` 定时发布。

发布前会打印一份摘要让你确认。确认无误再回车；管道或 CI 里没有交互，会直接中止，不会误发。确定要跳过确认就加 `--yes`。

### 看数据

```bash
dyctl posts --limit 20        # 自己的作品
dyctl metrics                 # 创作者中心概览
dyctl comments 7123456789012345678 --limit 50
```

要接自己的库就用 `--json`：

```bash
dyctl posts --json | jq '.[] | {id: .aweme_id, play: .stats.play, like: .stats.like}'
```

### 评论互动

```bash
# 回作品
dyctl reply 7123456789012345678 --text "感谢分享!"

# 回某条评论
dyctl reply 7123456789012345678 --text "确实是这样" --cid 7300000000000000000
```

同样，发送前要确认，`--yes` 跳过。

### 抓成 Markdown

```bash
# 单页
dyctl digest https://www.douyin.com/video/7123456789012345678

# 一个创作者的所有作品
dyctl collect --user MS4wLjABAAAAxxxx -n 20 -c 3

# 按关键词
dyctl collect --search "露营装备" -n 10

# 从任意含作品链接的页面开始
dyctl collect --from-url https://www.douyin.com/ -n 5
```

Markdown 存在 `~/.dyctl/downloads/`，可以直接喂给任何 LLM。

### 让 AI 直接分析

```bash
export DY_LLM_PROVIDER=deepseek/deepseek-chat
export DY_LLM_API_KEY=sk-xxxx

dyctl analyze https://www.douyin.com/user/MS4wLjABAAAAxxxx \
  --task "总结这个账号的选题方向和爆款的共同点"
```

LLM 只在 `analyze` 这一个命令里出现，浏览器操作不交给模型。

| 环境变量 | 说明 | 默认 |
|---|---|---|
| `DY_LLM_PROVIDER` | LiteLLM provider，如 `openai/gpt-4o-mini`、`deepseek/deepseek-chat`、`anthropic/claude-3-5-sonnet-20240620` | `openai/gpt-4o-mini` |
| `DY_LLM_API_KEY` | API Key。不设的话用厂商自己的环境变量，比如 `OPENAI_API_KEY` | — |
| `DY_LLM_BASE_URL` | 自定义端点 | — |
| `DY_LLM_MODEL` | 覆盖模型名 | 同 provider |

## 不想扫码

从浏览器插件导出 cookie 文件，喂给它就行。

```bash
dyctl cookies import ~/Downloads/cookies.txt    # 导一次，之后所有命令都用这份状态
dyctl cookies export ~/backup-cookies.txt        # 备份当前登录态
```

临时用一次也行：

```bash
dyctl --cookie-file ~/Downloads/cookies.txt posts
```

自动认这几种格式：

| 格式 | 长相 | 哪来的 |
|---|---|---|
| Netscape cookies.txt | `.douyin.com\tTRUE\t/\tFALSE\t1793696400\tsessionid\tabc` | Get cookies.txt LOCALLY / EditThisCookie / Cookie-Editor |
| JSON 数组 | `[{"domain":".douyin.com","name":"sessionid","value":"...","expirationDate":...}]` | Cookie-Editor / EditThisCookie 导 JSON |
| JSON 对象 | `{"sessionid":"...","sid_tt":"..."}` | 手写或其它工具 |
| Playwright storage_state | `{"cookies":[...],"origins":[...]}` | `dyctl cookies export` 存下来的 |

拿 cookie 文件的步骤：

1. Chrome 装 **Get cookies.txt LOCALLY** 或 **EditThisCookie**
2. 打开 `douyin.com`，确认已经登录
3. 插件里导出这个域名的 cookie
4. `dyctl cookies import <文件>`

只保留 `douyin.com` 域名的 cookie，别的站点自动丢掉。导入一次就写进本地 profile，以后不用再导。cookie 过期了重新导一次。

## 多账号

每个 profile 各自一份登录态，互不干扰。

```bash
dyctl --profile brand-a login
dyctl --profile brand-b login
dyctl --profile brand-a posts
```

profile 都在 `~/.dyctl/profiles/` 下。

## 出问题了

**命令卡住或者返回空内容，多半是没登录。** 未登录访问搜索、详情、评论相关的页面，抖音会把你丢到一个验证码中间页，那页上一条业务接口都不发。`dyctl status` 先确认一下。首页和热搜榜不登录也能访问。

**某个作品页只有导航骨架，正文是"视频数据加载中"。** 也是没登录，详情页的正文要登录态才渲染。

**被风控拦了。** 无头模式最容易被认出来，默认就是有头模式，`--headless` 只在调试时用。觉得动作太快就加 `--slow-mo 500`。

**抖音改版之后某个命令不准了。** 跑 `dyctl probe <页面URL>`，它会把页面上的真实 `data-e2e` 值、按钮文字、输入框占位符和实际调用的接口路径都打出来，照着改 `src/dyctl/selectors.py` 就行，不用猜。`dyctl shot <页面URL>` 可以截个图看看渲染成什么样。

**想看它到底干了什么。** `dyctl --verbose <命令>`。

## 写操作的安全约定

- 发布和发评论前一定打印摘要要求确认，`--yes` 跳过
- 管道、CI 这类没有交互的环境里，确认环节自动中止，不会误发
- 写完之后会校验结果（输入框有没有清空、URL 有没有跳转），确认不了就把现场截图存到 `~/.dyctl/artifacts/`

## 本地文件

```
~/.dyctl/
├── profiles/<name>/   浏览器 profile，登录态和指纹都在这儿
├── artifacts/         截图和失败现场
└── downloads/         抓下来的 Markdown
```

`dyctl logout` 会删掉对应 profile。

## 已知限制

- 抖音的 DOM 和接口路径都会随版本变。首次用某个页面时先 `dyctl probe` 核对一下。
- 抓数据走接口，但发作品和发评论还是靠点按钮，这两处最容易在改版后失效。
- 无头模式容易被风控识别，别拿它跑生产。
- 自动化操作可能违反抖音的用户协议，频率自己控制。

## 开发

```bash
uv run python tests/smoke_test.py           # 33 项，不需要登录态
uv run python tests/verify_crawl4ai_write.py # 6 项，验证写操作链路
uv run python tests/test_cookies.py          # 20 项，含真实浏览器注入
```

`uvx ruff@0.8.6 check src/ tests/` 是 lint 入口。
