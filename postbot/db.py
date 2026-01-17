"""Database layer for PostBot with connection pooling"""
import asyncio
import secrets
import json
import logging
from datetime import datetime
from typing import List, Optional, Tuple, Any
from contextlib import asynccontextmanager
import aiosqlite

from .models import Post, Template, Chat, User, Statistics, Participant, UrlButton

logger = logging.getLogger(__name__)


class Database:
    """SQLite database with connection pooling for better concurrency"""
    
    def __init__(self, path: str = "scheduler.db", pool_size: int = 10):
        self.path = path
        self.pool_size = pool_size
        self._pool: asyncio.Queue = None
        self._initialized = False

    async def init(self):
        """Initialize database and connection pool"""
        if self._initialized:
            return
            
        # Create schema first
        async with aiosqlite.connect(self.path) as db:
            await db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY, 
                    username TEXT, 
                    timezone TEXT DEFAULT 'Asia/Jerusalem', 
                    joined_date TEXT, 
                    web_token TEXT
                );
                
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY, 
                    chat_title TEXT, 
                    chat_type TEXT, 
                    owner_id INTEGER, 
                    added_date TEXT
                );
                
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    post_id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    chat_id INTEGER, 
                    owner_id INTEGER, 
                    content TEXT,
                    media_type TEXT, 
                    media_file_id TEXT, 
                    schedule_type TEXT, 
                    scheduled_time TEXT, 
                    scheduled_date TEXT,
                    days_of_week TEXT, 
                    day_of_month INTEGER,
                    is_active INTEGER DEFAULT 1, 
                    created_at TEXT, 
                    last_sent_at TEXT,
                    execution_count INTEGER DEFAULT 0, 
                    pin_post INTEGER DEFAULT 0, 
                    has_spoiler INTEGER DEFAULT 0,
                    has_participate_button INTEGER DEFAULT 0, 
                    button_text TEXT DEFAULT 'Участвовать',
                    url_buttons TEXT DEFAULT '[]', 
                    sent_message_id INTEGER, 
                    template_name TEXT,
                    reaction_buttons TEXT DEFAULT '[]'
                );
                
                CREATE TABLE IF NOT EXISTS reactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER,
                    button_id TEXT,
                    user_id INTEGER,
                    username TEXT,
                    reacted_at TEXT,
                    UNIQUE(post_id, button_id, user_id)
                );
                
                CREATE TABLE IF NOT EXISTS templates (
                    template_id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    owner_id INTEGER, 
                    name TEXT,
                    content TEXT, 
                    media_type TEXT, 
                    media_file_id TEXT, 
                    pin_post INTEGER DEFAULT 0,
                    has_spoiler INTEGER DEFAULT 0, 
                    has_participate_button INTEGER DEFAULT 0,
                    button_text TEXT DEFAULT 'Участвовать', 
                    url_buttons TEXT DEFAULT '[]', 
                    created_at TEXT
                );
                
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    post_id INTEGER, 
                    user_id INTEGER, 
                    username TEXT, 
                    joined_at TEXT, 
                    UNIQUE(post_id, user_id)
                );
                
                CREATE TABLE IF NOT EXISTS statistics (
                    stat_id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    user_id INTEGER UNIQUE, 
                    posts_created INTEGER DEFAULT 0, 
                    posts_sent INTEGER DEFAULT 0, 
                    posts_failed INTEGER DEFAULT 0, 
                    last_updated TEXT
                );
                
                CREATE TABLE IF NOT EXISTS post_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER,
                    sent_at TEXT,
                    chat_id INTEGER,
                    message_id INTEGER,
                    success INTEGER DEFAULT 1,
                    error_text TEXT
                );
                
                CREATE INDEX IF NOT EXISTS idx_posts_owner ON scheduled_posts(owner_id);
                CREATE INDEX IF NOT EXISTS idx_posts_active ON scheduled_posts(is_active);
                CREATE INDEX IF NOT EXISTS idx_participants_post ON participants(post_id);
            ''')
            
            # Run migrations
            migrations = [
                ("scheduled_posts", "day_of_month INTEGER"),
                ("scheduled_posts", "reaction_buttons TEXT DEFAULT '[]'"),
                ("users", "web_token TEXT"),
            ]
            for table, column in migrations:
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
                except:
                    pass
            
            # Create reactions table if not exists
            await db.execute('''CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                button_id TEXT,
                user_id INTEGER,
                username TEXT,
                reacted_at TEXT,
                UNIQUE(post_id, button_id, user_id)
            )''')
            await db.commit()
        
        # Initialize connection pool
        self._pool = asyncio.Queue(maxsize=self.pool_size)
        for _ in range(self.pool_size):
            conn = await aiosqlite.connect(self.path)
            conn.row_factory = aiosqlite.Row
            await self._pool.put(conn)
        
        self._initialized = True
        logger.info(f"Database initialized with pool size {self.pool_size}")

    @asynccontextmanager
    async def get_conn(self):
        """Get connection from pool"""
        conn = await self._pool.get()
        try:
            yield conn
        finally:
            await self._pool.put(conn)

    async def close(self):
        """Close all connections in pool"""
        if self._pool:
            while not self._pool.empty():
                conn = await self._pool.get()
                await conn.close()

    # ==================== Users ====================
    async def add_user(self, uid: int, username: str) -> str:
        token = secrets.token_urlsafe(32)
        async with self.get_conn() as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, username, joined_date, web_token) VALUES (?,?,?,?)",
                (uid, username, datetime.now().isoformat(), token)
            )
            await db.execute(
                "INSERT OR IGNORE INTO statistics (user_id, last_updated) VALUES (?,?)",
                (uid, datetime.now().isoformat())
            )
            await db.commit()
        return token

    async def get_user(self, uid: int) -> Optional[User]:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT * FROM users WHERE user_id=?", (uid,))
            row = await cur.fetchone()
            return User.from_row(tuple(row)) if row else None

    async def get_user_token(self, uid: int) -> Optional[str]:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT web_token FROM users WHERE user_id=?", (uid,))
            row = await cur.fetchone()
            return row[0] if row else None

    async def get_user_by_token(self, token: str) -> Optional[Tuple[int]]:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT user_id FROM users WHERE web_token=?", (token,))
            return await cur.fetchone()

    async def get_tz(self, uid: int) -> str:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT timezone FROM users WHERE user_id=?", (uid,))
            row = await cur.fetchone()
            return row[0] if row else "Asia/Jerusalem"

    async def set_tz(self, uid: int, tz: str):
        async with self.get_conn() as db:
            await db.execute("UPDATE users SET timezone=? WHERE user_id=?", (tz, uid))
            await db.commit()

    # ==================== Chats ====================
    async def add_chat(self, cid: int, title: str, ctype: str, owner: int):
        async with self.get_conn() as db:
            await db.execute(
                "INSERT OR REPLACE INTO chats VALUES (?,?,?,?,?)",
                (cid, title, ctype, owner, datetime.now().isoformat())
            )
            await db.commit()

    async def get_chats(self, uid: int) -> List[Chat]:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT * FROM chats WHERE owner_id=?", (uid,))
            rows = await cur.fetchall()
            return [Chat.from_row(tuple(r)) for r in rows]

    async def get_chat(self, cid: int) -> Optional[Chat]:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT * FROM chats WHERE chat_id=?", (cid,))
            row = await cur.fetchone()
            return Chat.from_row(tuple(row)) if row else None

    # ==================== Posts ====================
    async def add_post(self, **kw) -> int:
        async with self.get_conn() as db:
            cur = await db.execute('''
                INSERT INTO scheduled_posts (
                    chat_id, owner_id, content, media_type, media_file_id, schedule_type, 
                    scheduled_time, scheduled_date, days_of_week, day_of_month, created_at, 
                    pin_post, has_spoiler, has_participate_button, button_text, url_buttons, 
                    template_name, reaction_buttons
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (kw['chat_id'], kw['owner_id'], kw.get('content', ''), kw.get('media_type'),
                 kw.get('media_file_id'), kw.get('schedule_type'), kw.get('scheduled_time', ''),
                 kw.get('scheduled_date'), kw.get('days_of_week'), kw.get('day_of_month'),
                 datetime.now().isoformat(), kw.get('pin_post', 0), kw.get('has_spoiler', 0),
                 kw.get('has_participate', 0), kw.get('button_text', 'Участвовать'),
                 kw.get('url_buttons', '[]'), kw.get('template_name'), kw.get('reaction_buttons', '[]'))
            )
            await db.commit()
            return cur.lastrowid

    async def get_post(self, pid: int) -> Optional[Post]:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT * FROM scheduled_posts WHERE post_id=?", (pid,))
            row = await cur.fetchone()
            return Post.from_row(tuple(row)) if row else None

    async def get_posts(self, uid: int, filter_type: str = "all", limit: int = 50, offset: int = 0) -> List[Post]:
        async with self.get_conn() as db:
            where = "owner_id=?"
            params = [uid]
            if filter_type == "active":
                where += " AND is_active=1"
            elif filter_type == "inactive":
                where += " AND is_active=0"
            cur = await db.execute(
                f"SELECT * FROM scheduled_posts WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset)
            )
            rows = await cur.fetchall()
            return [Post.from_row(tuple(r)) for r in rows]

    async def count_posts(self, uid: int, filter_type: str = "all") -> int:
        async with self.get_conn() as db:
            where = "owner_id=?"
            params = [uid]
            if filter_type == "active":
                where += " AND is_active=1"
            elif filter_type == "inactive":
                where += " AND is_active=0"
            cur = await db.execute(f"SELECT COUNT(*) FROM scheduled_posts WHERE {where}", params)
            row = await cur.fetchone()
            return row[0] if row else 0

    async def update_post(self, pid: int, **kw):
        if not kw:
            return
        async with self.get_conn() as db:
            sets = ",".join(f"{k}=?" for k in kw)
            await db.execute(f"UPDATE scheduled_posts SET {sets} WHERE post_id=?", (*kw.values(), pid))
            await db.commit()

    async def delete_post(self, pid: int):
        async with self.get_conn() as db:
            await db.execute("DELETE FROM scheduled_posts WHERE post_id=?", (pid,))
            await db.execute("DELETE FROM participants WHERE post_id=?", (pid,))
            await db.commit()

    async def delete_posts_bulk(self, uid: int, filter_type: str = "all"):
        async with self.get_conn() as db:
            where = "owner_id=?"
            params = [uid]
            if filter_type == "active":
                where += " AND is_active=1"
            elif filter_type == "inactive":
                where += " AND is_active=0"
            await db.execute(f"DELETE FROM scheduled_posts WHERE {where}", params)
            await db.commit()

    async def disable_posts_bulk(self, uid: int):
        async with self.get_conn() as db:
            await db.execute("UPDATE scheduled_posts SET is_active=0 WHERE owner_id=?", (uid,))
            await db.commit()

    async def get_active_posts(self) -> List[Tuple[int]]:
        async with self.get_conn() as db:
            cur = await db.execute(
                "SELECT post_id FROM scheduled_posts WHERE is_active=1 AND schedule_type!='instant'"
            )
            return await cur.fetchall()

    async def duplicate_post(self, pid: int) -> Optional[int]:
        post = await self.get_post(pid)
        if not post:
            return None
        return await self.add_post(
            chat_id=post.chat_id, owner_id=post.owner_id, content=post.content,
            media_type=post.media_type, media_file_id=post.media_file_id,
            schedule_type=post.schedule_type, scheduled_time=post.scheduled_time,
            scheduled_date=post.scheduled_date, days_of_week=post.days_of_week,
            day_of_month=post.day_of_month, pin_post=post.pin_post,
            has_spoiler=post.has_spoiler, has_participate=post.has_participate_button,
            button_text=post.button_text, url_buttons=post.url_buttons_json(),
            template_name=post.template_name
        )

    # ==================== Templates ====================
    async def add_template(self, owner_id: int, name: str, content: str, media_type: str = None,
                          media_file_id: str = None, pin: int = 0, spoiler: int = 0,
                          participate: int = 0, btn_text: str = "Участвовать", url_btns: str = "[]"):
        async with self.get_conn() as db:
            await db.execute('''
                INSERT INTO templates (owner_id, name, content, media_type, media_file_id, pin_post, 
                    has_spoiler, has_participate_button, button_text, url_buttons, created_at) 
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (owner_id, name, content, media_type, media_file_id, pin, spoiler, participate,
                 btn_text, url_btns, datetime.now().isoformat())
            )
            await db.commit()

    async def get_templates(self, uid: int) -> List[Template]:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT * FROM templates WHERE owner_id=?", (uid,))
            rows = await cur.fetchall()
            return [Template.from_row(tuple(r)) for r in rows]

    async def get_template(self, tid: int) -> Optional[Template]:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT * FROM templates WHERE template_id=?", (tid,))
            row = await cur.fetchone()
            return Template.from_row(tuple(row)) if row else None

    async def delete_template(self, tid: int):
        async with self.get_conn() as db:
            await db.execute("DELETE FROM templates WHERE template_id=?", (tid,))
            await db.commit()

    # ==================== Statistics ====================
    async def get_stats(self, uid: int) -> Optional[Statistics]:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT * FROM statistics WHERE user_id=?", (uid,))
            row = await cur.fetchone()
            return Statistics.from_row(tuple(row)) if row else None

    async def update_stats(self, uid: int, created: int = 0, sent: int = 0, failed: int = 0):
        async with self.get_conn() as db:
            await db.execute(
                "UPDATE statistics SET posts_created=posts_created+?, posts_sent=posts_sent+?, "
                "posts_failed=posts_failed+?, last_updated=? WHERE user_id=?",
                (created, sent, failed, datetime.now().isoformat(), uid)
            )
            await db.commit()

    # ==================== Participants ====================
    async def add_participant(self, pid: int, uid: int, uname: str) -> bool:
        try:
            async with self.get_conn() as db:
                await db.execute(
                    "INSERT INTO participants VALUES (NULL,?,?,?,?)",
                    (pid, uid, uname, datetime.now().isoformat())
                )
                await db.commit()
                return True
        except:
            return False

    async def count_participants(self, pid: int) -> int:
        async with self.get_conn() as db:
            cur = await db.execute("SELECT COUNT(*) FROM participants WHERE post_id=?", (pid,))
            row = await cur.fetchone()
            return row[0] if row else 0

    async def get_participants(self, pid: int, limit: int = 100, offset: int = 0) -> List[Participant]:
        async with self.get_conn() as db:
            cur = await db.execute(
                "SELECT * FROM participants WHERE post_id=? ORDER BY joined_at DESC LIMIT ? OFFSET ?",
                (pid, limit, offset)
            )
            rows = await cur.fetchall()
            return [Participant.from_row(tuple(r)) for r in rows]

    # ==================== History ====================
    async def add_history(self, pid: int, cid: int, mid: int, success: bool = True, error: str = None):
        async with self.get_conn() as db:
            await db.execute(
                "INSERT INTO post_history (post_id, sent_at, chat_id, message_id, success, error_text) VALUES (?,?,?,?,?,?)",
                (pid, datetime.now().isoformat(), cid, mid, int(success), error)
            )
            await db.commit()

    # ==================== Reactions ====================
    async def add_reaction(self, pid: int, button_id: str, uid: int, uname: str) -> bool:
        """Add user reaction to a button. Returns True if new, False if already exists."""
        try:
            async with self.get_conn() as db:
                await db.execute(
                    "INSERT INTO reactions (post_id, button_id, user_id, username, reacted_at) VALUES (?,?,?,?,?)",
                    (pid, button_id, uid, uname, datetime.now().isoformat())
                )
                await db.commit()
                return True
        except:
            return False

    async def remove_reaction(self, pid: int, button_id: str, uid: int) -> bool:
        """Remove user reaction from a button."""
        async with self.get_conn() as db:
            cur = await db.execute(
                "DELETE FROM reactions WHERE post_id=? AND button_id=? AND user_id=?",
                (pid, button_id, uid)
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_user_reaction(self, pid: int, uid: int) -> Optional[str]:
        """Get button_id user reacted to (if any)."""
        async with self.get_conn() as db:
            cur = await db.execute(
                "SELECT button_id FROM reactions WHERE post_id=? AND user_id=?",
                (pid, uid)
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def count_reactions(self, pid: int, button_id: str) -> int:
        """Count reactions for a specific button."""
        async with self.get_conn() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM reactions WHERE post_id=? AND button_id=?",
                (pid, button_id)
            )
            row = await cur.fetchone()
            return row[0] if row else 0

    async def get_all_reaction_counts(self, pid: int) -> dict:
        """Get all reaction counts for a post as {button_id: count}."""
        async with self.get_conn() as db:
            cur = await db.execute(
                "SELECT button_id, COUNT(*) FROM reactions WHERE post_id=? GROUP BY button_id",
                (pid,)
            )
            rows = await cur.fetchall()
            return {row[0]: row[1] for row in rows}

    # ==================== Export/Import ====================
    async def export_posts(self, uid: int) -> List[dict]:
        posts = await self.get_posts(uid, limit=1000)
        return [{
            "content": p.content, "media_type": p.media_type, "schedule_type": p.schedule_type,
            "scheduled_time": p.scheduled_time, "scheduled_date": p.scheduled_date,
            "days_of_week": p.days_of_week, "day_of_month": p.day_of_month,
            "pin_post": int(p.pin_post), "has_spoiler": int(p.has_spoiler),
            "has_participate": int(p.has_participate_button), "button_text": p.button_text,
            "url_buttons": p.url_buttons_json()
        } for p in posts]
