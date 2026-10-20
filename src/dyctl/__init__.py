"""抖音网页端自动化 CLI。

分层设计：
    config     运行期配置与路径
    session    Playwright 浏览器会话生命周期（持久化 profile + 反检测）
    api        拦截前端自身发出的 JSON 响应，绕开 a_bogus 签名
    selectors  选择器集中注册表（DOM 变化时唯一需要改的地方）
    commands   业务命令实现
    cli        Typer 命令行入口
"""

__version__ = "0.1.0"
