"""数据模型：从抖音 JSON 响应中归一化出的结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .api import dig, pick
from .utils import clean_text, ts_to_str


@dataclass(slots=True)
class Stats:
    play: int = 0
    like: int = 0
    comment: int = 0
    share: int = 0
    collect: int = 0

    @classmethod
    def from_payload(cls, raw: Any) -> Stats:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            play=_int(pick(raw, "play_count", "playCount", "vv_count", default=0)),
            like=_int(pick(raw, "digg_count", "diggCount", default=0)),
            comment=_int(pick(raw, "comment_count", "commentCount", default=0)),
            share=_int(pick(raw, "share_count", "shareCount", default=0)),
            collect=_int(pick(raw, "collect_count", "collectCount", default=0)),
        )

    def as_dict(self) -> dict:
        return {
            "play": self.play,
            "like": self.like,
            "comment": self.comment,
            "share": self.share,
            "collect": self.collect,
        }


@dataclass(slots=True)
class Post:
    aweme_id: str = ""
    title: str = ""
    author: str = ""
    author_id: str = ""
    create_time: int = 0
    stats: Stats = field(default_factory=Stats)
    cover_url: str = ""
    url: str = ""

    @classmethod
    def from_payload(cls, raw: dict) -> Post:
        aweme_id = str(pick(raw, "aweme_id", "awemeId", "group_id", default=""))
        desc = clean_text(pick(raw, "desc", "title", "description", default=""))
        author = pick(raw, "author", default={}) or {}
        stats_raw = pick(raw, "statistics", "stats", default={}) or {}
        # 列表接口与详情接口的 statistics 层级不一致，逐个兜底
        if not isinstance(stats_raw, dict) or not stats_raw:
            stats_raw = raw
        create_time = _int(pick(raw, "create_time", "createTime", default=0))
        return cls(
            aweme_id=aweme_id,
            title=desc,
            author=clean_text(pick(author, "nickname", "nick_name", default="")),
            author_id=str(pick(author, "uid", "sec_uid", "unique_id", default="")),
            create_time=create_time,
            stats=Stats.from_payload(stats_raw),
            cover_url=_cover(raw),
            url=f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "",
        )

    def as_dict(self) -> dict:
        return {
            "aweme_id": self.aweme_id,
            "title": self.title,
            "author": self.author,
            "author_id": self.author_id,
            "create_time": self.create_time,
            "create_time_str": ts_to_str(self.create_time),
            "stats": self.stats.as_dict(),
            "cover_url": self.cover_url,
            "url": self.url,
        }


@dataclass(slots=True)
class Comment:
    cid: str = ""
    text: str = ""
    author: str = ""
    author_id: str = ""
    create_time: int = 0
    like: int = 0
    reply_count: int = 0
    is_author: bool = False

    @classmethod
    def from_payload(cls, raw: dict) -> Comment:
        author = pick(raw, "user", default={}) or {}
        return cls(
            cid=str(pick(raw, "cid", "comment_id", default="")),
            text=clean_text(pick(raw, "text", "content", default="")),
            author=clean_text(pick(author, "nickname", "nick_name", default="")),
            author_id=str(pick(author, "uid", "sec_uid", default="")),
            create_time=_int(pick(raw, "create_time", "createTime", default=0)),
            like=_int(pick(raw, "digg_count", "diggCount", default=0)),
            reply_count=_int(pick(raw, "reply_comment_total", "reply_comment_total", default=0)),
            is_author=bool(pick(raw, "is_author", default=False)),
        )

    def as_dict(self) -> dict:
        return {
            "cid": self.cid,
            "text": self.text,
            "author": self.author,
            "author_id": self.author_id,
            "create_time": self.create_time,
            "create_time_str": ts_to_str(self.create_time),
            "like": self.like,
            "reply_count": self.reply_count,
            "is_author": self.is_author,
        }


@dataclass(slots=True)
class UserProfile:
    uid: str = ""
    nickname: str = ""
    unique_id: str = ""
    sec_uid: str = ""
    signature: str = ""
    follower: int = 0
    following: int = 0
    total_favorited: int = 0
    aweme_count: int = 0
    avatar: str = ""

    @classmethod
    def from_payload(cls, raw: dict) -> UserProfile:
        user = pick(raw, "user", "user_info", default=raw) or {}
        if not isinstance(user, dict):
            user = {}
        return cls(
            uid=str(pick(user, "uid", "id", default="")),
            nickname=clean_text(pick(user, "nickname", default="")),
            unique_id=clean_text(pick(user, "unique_id", "short_id", default="")),
            sec_uid=str(pick(user, "sec_uid", default="")),
            signature=clean_text(pick(user, "signature", default="")),
            follower=_int(pick(user, "follower_count", "mplatform_followers_count", default=0)),
            following=_int(pick(user, "following_count", default=0)),
            total_favorited=_int(pick(user, "total_favorited", default=0)),
            aweme_count=_int(pick(user, "aweme_count", default=0)),
            avatar=str(pick(user, "avatar_larger", "avatar_thumb", "avatar_medium", default="")),
        )

    def as_dict(self) -> dict:
        return {
            "uid": self.uid,
            "nickname": self.nickname,
            "unique_id": self.unique_id,
            "sec_uid": self.sec_uid,
            "signature": self.signature,
            "follower": self.follower,
            "following": self.following,
            "total_favorited": self.total_favorited,
            "aweme_count": self.aweme_count,
            "avatar": self.avatar,
        }


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cover(raw: dict) -> str:
    candidates = [
        dig(raw, "video", "cover", "url_list", 0),
        dig(raw, "video", "origin_cover", "url_list", 0),
        dig(raw, "video", "dynamic_cover", "url_list", 0),
        dig(raw, "cover", "url_list", 0),
        dig(raw, "origin_cover", "url_list", 0),
        dig(raw, "video", "originCover", "urlList", 0),
    ]
    for c in candidates:
        if c:
            return str(c)
    return ""
