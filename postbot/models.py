"""Data models for PostBot"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum
import json


class ScheduleType(str, Enum):
    INSTANT = "instant"
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class MediaType(str, Enum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"


@dataclass
class UrlButton:
    text: str
    url: str


@dataclass
class ReactionButton:
    """Button for voting/reactions (👍, 👎, За, Против, etc.)"""
    id: str  # unique id for this button
    text: str  # display text with emoji
    count: int = 0


@dataclass
class Post:
    post_id: int
    chat_id: int
    owner_id: int
    content: str = ""
    media_type: Optional[str] = None
    media_file_id: Optional[str] = None
    schedule_type: str = "once"
    scheduled_time: str = ""
    scheduled_date: Optional[str] = None
    days_of_week: Optional[str] = None
    day_of_month: Optional[int] = None
    is_active: bool = True
    created_at: str = ""
    last_sent_at: Optional[str] = None
    execution_count: int = 0
    pin_post: bool = False
    has_spoiler: bool = False
    has_participate_button: bool = False
    button_text: str = "Участвовать"
    url_buttons: List[UrlButton] = field(default_factory=list)
    sent_message_id: Optional[int] = None
    template_name: Optional[str] = None
    reaction_buttons: List[ReactionButton] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: tuple) -> "Post":
        if not row:
            return None
        url_btns = []
        if row[19]:
            try:
                url_btns = [UrlButton(**b) for b in json.loads(row[19])]
            except:
                pass
        reaction_btns = []
        if len(row) > 22 and row[22]:
            try:
                reaction_btns = [ReactionButton(**b) for b in json.loads(row[22])]
            except:
                pass
        return cls(
            post_id=row[0], chat_id=row[1], owner_id=row[2], content=row[3] or "",
            media_type=row[4], media_file_id=row[5], schedule_type=row[6] or "once",
            scheduled_time=row[7] or "", scheduled_date=row[8], days_of_week=row[9],
            day_of_month=row[10], is_active=bool(row[11]), created_at=row[12] or "",
            last_sent_at=row[13], execution_count=row[14] or 0, pin_post=bool(row[15]),
            has_spoiler=bool(row[16]), has_participate_button=bool(row[17]),
            button_text=row[18] or "Участвовать", url_buttons=url_btns,
            sent_message_id=row[20], template_name=row[21], reaction_buttons=reaction_btns
        )

    def url_buttons_json(self) -> str:
        return json.dumps([{"text": b.text, "url": b.url} for b in self.url_buttons])

    def reaction_buttons_json(self) -> str:
        return json.dumps([{"id": b.id, "text": b.text, "count": b.count} for b in self.reaction_buttons])


@dataclass
class Template:
    template_id: int
    owner_id: int
    name: str
    content: str = ""
    media_type: Optional[str] = None
    media_file_id: Optional[str] = None
    pin_post: bool = False
    has_spoiler: bool = False
    has_participate_button: bool = False
    button_text: str = "Участвовать"
    url_buttons: List[UrlButton] = field(default_factory=list)
    created_at: str = ""

    @classmethod
    def from_row(cls, row: tuple) -> "Template":
        if not row:
            return None
        url_btns = []
        if row[10]:
            try:
                url_btns = [UrlButton(**b) for b in json.loads(row[10])]
            except:
                pass
        return cls(
            template_id=row[0], owner_id=row[1], name=row[2], content=row[3] or "",
            media_type=row[4], media_file_id=row[5], pin_post=bool(row[6]),
            has_spoiler=bool(row[7]), has_participate_button=bool(row[8]),
            button_text=row[9] or "Участвовать", url_buttons=url_btns,
            created_at=row[11] or ""
        )


@dataclass
class Chat:
    chat_id: int
    chat_title: str
    chat_type: str
    owner_id: int
    added_date: str = ""

    @classmethod
    def from_row(cls, row: tuple) -> "Chat":
        if not row:
            return None
        return cls(chat_id=row[0], chat_title=row[1], chat_type=row[2], owner_id=row[3], added_date=row[4] or "")


@dataclass
class User:
    user_id: int
    username: Optional[str] = None
    timezone: str = "Asia/Jerusalem"
    joined_date: str = ""
    web_token: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "User":
        if not row:
            return None
        return cls(user_id=row[0], username=row[1], timezone=row[2] or "Asia/Jerusalem",
                   joined_date=row[3] or "", web_token=row[4])


@dataclass
class Statistics:
    stat_id: int
    user_id: int
    posts_created: int = 0
    posts_sent: int = 0
    posts_failed: int = 0
    last_updated: str = ""

    @classmethod
    def from_row(cls, row: tuple) -> "Statistics":
        if not row:
            return None
        return cls(stat_id=row[0], user_id=row[1], posts_created=row[2] or 0,
                   posts_sent=row[3] or 0, posts_failed=row[4] or 0, last_updated=row[5] or "")


@dataclass
class Participant:
    id: int
    post_id: int
    user_id: int
    username: str
    joined_at: str = ""

    @classmethod
    def from_row(cls, row: tuple) -> "Participant":
        if not row:
            return None
        return cls(id=row[0], post_id=row[1], user_id=row[2], username=row[3] or "", joined_at=row[4] or "")
