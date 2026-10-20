"""运行期配置。

所有本地状态都放在一个 home 目录下，默认 ~/.dyctl：
    profiles/<name>/   持久化浏览器 profile（cookie / localStorage / 设备指纹）
    artifacts/         截图、dump、失败现场
    downloads/         下载的视频与封面
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_home() -> Path:
    env = os.environ.get("DOUYIN_WEB_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".dyctl"


@dataclass(slots=True)
class Settings:
    """一次运行所使用的全部配置。"""

    home: Path = None  # type: ignore[assignment]
    default_profile: str = "default"

    # 浏览器
    headless: bool = False
    # 优先使用系统真实 Chrome（比打包的 Chromium 更不容易被风控识别）
    channel: str | None = "chrome"
    slow_mo: int = 0
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"
    viewport_width: int = 1440
    viewport_height: int = 900

    # 超时
    nav_timeout_ms: int = 45_000
    action_timeout_ms: int = 20_000
    api_wait_seconds: float = 25.0
    # 登录时等待扫码的秒数
    login_wait_seconds: int = 180

    def __post_init__(self) -> None:
        if self.home is None:
            self.home = _default_home()
        self.home = Path(self.home).expanduser()

    # ---- 路径 ----

    @property
    def profiles_dir(self) -> Path:
        return self.home / "profiles"

    @property
    def artifacts_dir(self) -> Path:
        return self.home / "artifacts"

    @property
    def downloads_dir(self) -> Path:
        return self.home / "downloads"

    def profile_dir(self, name: str) -> Path:
        return self.profiles_dir / name

    def ensure_dirs(self) -> None:
        for d in (self.profiles_dir, self.artifacts_dir, self.downloads_dir):
            d.mkdir(parents=True, exist_ok=True)

    def artifact_path(self, stem: str, suffix: str = ".png") -> Path:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        return self.artifacts_dir / f"{stem}{suffix}"


def load_settings() -> Settings:
    """从环境变量读取少量可调项，其余保持默认值。"""
    s = Settings()
    if v := os.environ.get("DOUYIN_WEB_HEADLESS"):
        s.headless = v.lower() in {"1", "true", "yes", "on"}
    if v := os.environ.get("DOUYIN_WEB_CHANNEL"):
        s.channel = None if v.lower() in {"", "none", "chromium"} else v
    if v := os.environ.get("DOUYIN_WEB_PROFILE"):
        s.default_profile = v
    if v := os.environ.get("DOUYIN_WEB_SLOW_MO"):
        try:
            s.slow_mo = int(v)
        except ValueError:
            pass
    return s
