import asyncio
import logging
import calendar
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode, ChatType
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
import aiosqlite
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import os
import re
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class Database:
    def __init__(self, db_path="scheduler.db"):
        self.db_path = db_path
    
    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    timezone TEXT DEFAULT 'Asia/Jerusalem',
                    joined_date TIMESTAMP
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY,
                    chat_title TEXT,
                    chat_type TEXT,
                    owner_id INTEGER,
                    added_date TIMESTAMP,
                    FOREIGN KEY (owner_id) REFERENCES users (user_id)
                )
            ''')
            await db.execute('''
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
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP,
                    last_sent_at TIMESTAMP,
                    execution_count INTEGER DEFAULT 0,
                    pin_post BOOLEAN DEFAULT 0,
                    has_spoiler BOOLEAN DEFAULT 0,
                    has_participate_button BOOLEAN DEFAULT 0,
                    button_text TEXT DEFAULT 'Участвовать',
                    url_buttons TEXT DEFAULT '[]',
                    sent_message_id INTEGER,
                    FOREIGN KEY (chat_id) REFERENCES chats (chat_id),
                    FOREIGN KEY (owner_id) REFERENCES users (user_id)
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER,
                    user_id INTEGER,
                    username TEXT,
                    joined_at TIMESTAMP,
                    UNIQUE(post_id, user_id),
                    FOREIGN KEY (post_id) REFERENCES scheduled_posts (post_id)
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS statistics (
                    stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    posts_created INTEGER DEFAULT 0,
                    posts_sent INTEGER DEFAULT 0,
                    posts_failed INTEGER DEFAULT 0,
                    last_updated TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            migrations = [
                "ALTER TABLE scheduled_posts ADD COLUMN pin_post BOOLEAN DEFAULT 0",
                "ALTER TABLE scheduled_posts ADD COLUMN has_spoiler BOOLEAN DEFAULT 0",
                "ALTER TABLE scheduled_posts ADD COLUMN has_participate_button BOOLEAN DEFAULT 0",
                "ALTER TABLE scheduled_posts ADD COLUMN button_text TEXT DEFAULT 'Участвовать'",
                "ALTER TABLE scheduled_posts ADD COLUMN url_buttons TEXT DEFAULT '[]'",
                "ALTER TABLE scheduled_posts ADD COLUMN sent_message_id INTEGER",
            ]
            for migration in migrations:
                try:
                    await db.execute(migration)
                except:
                    pass
            await db.commit()
    
    async def add_user(self, user_id: int, username: str = None, timezone: str = 'Asia/Jerusalem'):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, username, timezone, joined_date) VALUES (?, ?, ?, ?)",
                (user_id, username, timezone, datetime.now().isoformat())
            )
            await db.execute(
                "INSERT OR IGNORE INTO statistics (user_id, last_updated) VALUES (?, ?)",
                (user_id, datetime.now().isoformat())
            )
            await db.commit()
    
    async def set_user_timezone(self, user_id: int, timezone: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (timezone, user_id))
            await db.commit()
    
    async def get_user_timezone(self, user_id: int) -> str:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT timezone FROM users WHERE user_id = ?", (user_id,))
            result = await cursor.fetchone()
            return result[0] if result else 'Asia/Jerusalem'
    
    async def add_chat(self, chat_id: int, chat_title: str, chat_type: str, owner_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                '''INSERT OR REPLACE INTO chats 
                   (chat_id, chat_title, chat_type, owner_id, added_date) 
                   VALUES (?, ?, ?, ?, ?)''',
                (chat_id, chat_title, chat_type, owner_id, datetime.now().isoformat())
            )
            await db.commit()
    
    async def get_user_chats(self, user_id: int) -> List[tuple]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM chats WHERE owner_id = ? ORDER BY added_date DESC",
                (user_id,)
            )
            return await cursor.fetchall()
    
    async def add_scheduled_post(self, chat_id: int, owner_id: int, content: str, media_type: str,
        media_file_id: str, schedule_type: str, scheduled_time: str, scheduled_date: str = None,
        days_of_week: str = None, pin_post: bool = False, has_spoiler: bool = False,
        has_participate_button: bool = False, button_text: str = "Участвовать", url_buttons: str = "[]") -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''INSERT INTO scheduled_posts 
                   (chat_id, owner_id, content, media_type, media_file_id, 
                    schedule_type, scheduled_time, scheduled_date, days_of_week, 
                    created_at, pin_post, has_spoiler, has_participate_button, button_text, url_buttons)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (chat_id, owner_id, content, media_type, media_file_id,
                 schedule_type, scheduled_time, scheduled_date, days_of_week, 
                 datetime.now().isoformat(), pin_post, has_spoiler, has_participate_button, button_text, url_buttons)
            )
            await db.commit()
            return cursor.lastrowid
    
    async def get_user_scheduled_posts(self, user_id: int) -> List[tuple]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM scheduled_posts WHERE owner_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            return await cursor.fetchall()
    
    async def get_post_by_id(self, post_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT * FROM scheduled_posts WHERE post_id = ?", (post_id,))
            return await cursor.fetchone()
    
    async def update_post(self, post_id: int, **kwargs):
        async with aiosqlite.connect(self.db_path) as db:
            set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
            values = list(kwargs.values()) + [post_id]
            await db.execute(f"UPDATE scheduled_posts SET {set_clause} WHERE post_id = ?", values)
            await db.commit()
    
    async def delete_post(self, post_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM scheduled_posts WHERE post_id = ?", (post_id,))
            await db.commit()
    
    async def deactivate_post(self, post_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE scheduled_posts SET is_active = 0 WHERE post_id = ?", (post_id,))
            await db.commit()
    
    async def increment_post_counter(self, post_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE scheduled_posts SET execution_count = execution_count + 1, last_sent_at = ? WHERE post_id = ?",
                (datetime.now().isoformat(), post_id)
            )
            await db.commit()
    
    async def get_user_statistics(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT * FROM statistics WHERE user_id = ?", (user_id,))
            return await cursor.fetchone()
    
    async def update_statistics(self, user_id: int, posts_created: int = 0, posts_sent: int = 0, posts_failed: int = 0):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                '''UPDATE statistics 
                   SET posts_created = posts_created + ?, 
                       posts_sent = posts_sent + ?, 
                       posts_failed = posts_failed + ?,
                       last_updated = ?
                   WHERE user_id = ?''',
                (posts_created, posts_sent, posts_failed, datetime.now().isoformat(), user_id)
            )
            await db.commit()
    
    async def add_participant(self, post_id: int, user_id: int, username: str):
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO participants (post_id, user_id, username, joined_at) VALUES (?, ?, ?, ?)",
                    (post_id, user_id, username, datetime.now().isoformat())
                )
                await db.commit()
                return True
            except:
                return False
    
    async def get_participants_count(self, post_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM participants WHERE post_id = ?", (post_id,))
            result = await cursor.fetchone()
            return result[0] if result else 0


class PostStates(StatesGroup):
    waiting_for_content = State()
    waiting_for_schedule_type = State()
    waiting_for_time = State()
    waiting_for_date = State()
    waiting_for_days = State()
    waiting_for_media = State()
    configuring_post = State()
    confirming_post = State()
    waiting_for_url_button = State()
    editing_post = State()
    waiting_for_edit_content = State()
    waiting_for_add_media = State()
    waiting_for_edit_url_button = State()


class SchedulerBot:
    def __init__(self, token: str):
        self.bot = Bot(token=token)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.db = Database()
        self.router = Router()
        self.dp.include_router(self.router)
        self.scheduler = AsyncIOScheduler()
        self.register_handlers()
    
    def register_handlers(self):
        self.router.message.register(self.start_command, Command("start"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.help_command, Command("help"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.my_chats_command, Command("chats"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.new_post_command, Command("newpost"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.my_posts_command, Command("myposts"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.set_timezone_command, Command("timezone"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.stats_command, Command("stats"), F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.ignore_group_commands, Command("start", "help", "chats", "newpost", "myposts", "timezone", "stats"), F.chat.type != ChatType.PRIVATE)
        self.router.my_chat_member.register(self.on_bot_added)
        self.router.message.register(self.process_content, PostStates.waiting_for_content, F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.process_media, PostStates.waiting_for_media, F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.process_time_input, PostStates.waiting_for_time, F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.process_url_button_input, PostStates.waiting_for_url_button, F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.process_edit_content, PostStates.waiting_for_edit_content, F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.process_add_media, PostStates.waiting_for_add_media, F.chat.type == ChatType.PRIVATE)
        self.router.message.register(self.process_edit_url_button_input, PostStates.waiting_for_edit_url_button, F.chat.type == ChatType.PRIVATE)
        self.router.callback_query.register(self.process_callback)
    
    async def ignore_group_commands(self, message: Message):
        pass
    
    def generate_calendar(self, year: int, month: int) -> InlineKeyboardMarkup:
        keyboard = []
        month_names = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                       "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
        keyboard.append([
            InlineKeyboardButton(text="◀️", callback_data=f"cal_prev_{year}_{month}"),
            InlineKeyboardButton(text=f"{month_names[month]} {year}", callback_data="cal_ignore"),
            InlineKeyboardButton(text="▶️", callback_data=f"cal_next_{year}_{month}")
        ])
        days_header = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        keyboard.append([InlineKeyboardButton(text=d, callback_data="cal_ignore") for d in days_header])
        cal = calendar.monthcalendar(year, month)
        today = datetime.now()
        for week in cal:
            row = []
            for day in week:
                if day == 0:
                    row.append(InlineKeyboardButton(text=" ", callback_data="cal_ignore"))
                else:
                    date_obj = datetime(year, month, day)
                    if date_obj.date() < today.date():
                        row.append(InlineKeyboardButton(text="·", callback_data="cal_ignore"))
                    else:
                        row.append(InlineKeyboardButton(text=str(day), callback_data=f"cal_day_{year}_{month}_{day}"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_post")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    def generate_time_picker(self, for_daily: bool = False, selected_times: list = None) -> InlineKeyboardMarkup:
        keyboard = []
        hours = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
        selected_times = selected_times or []
        for i in range(0, len(hours), 4):
            row = []
            for h in hours[i:i+4]:
                time_str = f"{h:02d}:00"
                is_selected = time_str in selected_times
                text = f"✅ {time_str}" if is_selected else time_str
                row.append(InlineKeyboardButton(text=text, callback_data=f"time_{h:02d}_00"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton(text="⌨️ Ввести вручную", callback_data="time_manual")])
        if for_daily and selected_times:
            keyboard.append([InlineKeyboardButton(text=f"✅ Готово ({len(selected_times)} времён)", callback_data="times_done")])
        keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_post")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    async def safe_edit(self, message, text: str = None, reply_markup = None, parse_mode = ParseMode.HTML, **kwargs):
        try:
            if text:
                return await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs)
            else:
                return await message.edit_reply_markup(reply_markup=reply_markup)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
    
    def generate_post_settings_keyboard(self, data: dict) -> InlineKeyboardMarkup:
        pin = data.get("pin_post", False)
        spoiler = data.get("has_spoiler", False)
        participate = data.get("has_participate_button", False)
        media_type = data.get("content_type", "text")
        url_buttons = data.get("url_buttons", [])
        has_media = bool(data.get("media_file_id"))
        keyboard = [[InlineKeyboardButton(text=f"{'✅' if pin else '⬜️'} Закрепить пост", callback_data="toggle_pin")]]
        if media_type in ["photo", "video"] or has_media:
            keyboard.append([InlineKeyboardButton(text=f"{'✅' if spoiler else '⬜️'} Спойлер на медиа", callback_data="toggle_spoiler")])
        keyboard.append([InlineKeyboardButton(text=f"{'✅' if participate else '⬜️'} Кнопка «Участвовать»", callback_data="toggle_participate")])
        btn_count = len(url_buttons) if isinstance(url_buttons, list) else 0
        keyboard.append([InlineKeyboardButton(text=f"🔗 URL кнопки ({btn_count})", callback_data="manage_url_buttons")])
        if media_type == "text" and not has_media:
            keyboard.append([InlineKeyboardButton(text="🖼 Добавить медиа", callback_data="add_media_to_post")])
        keyboard.append([InlineKeyboardButton(text="👁 Предпросмотр", callback_data="preview_post"), InlineKeyboardButton(text="✅ Сохранить", callback_data="save_post")])
        keyboard.append([InlineKeyboardButton(text="🚀 Опубликовать сейчас", callback_data="publish_now")])
        keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_post")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    def build_post_keyboard(self, post_id: int, has_participate: bool, button_text: str, url_buttons: list, participants_count: int = 0) -> Optional[InlineKeyboardMarkup]:
        keyboard = []
        for btn in url_buttons:
            if isinstance(btn, dict) and btn.get("text") and btn.get("url"):
                keyboard.append([InlineKeyboardButton(text=btn["text"], url=btn["url"])])
        if has_participate:
            keyboard.append([InlineKeyboardButton(text=f"{button_text} ({participants_count})", callback_data=f"participate_{post_id}")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None
    
    async def start_command(self, message: Message):
        user_id = message.from_user.id
        username = message.from_user.username
        await self.db.add_user(user_id, username)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои чаты", callback_data="show_chats")],
            [InlineKeyboardButton(text="📝 Создать пост", callback_data="start_new_post")],
            [InlineKeyboardButton(text="📊 Мои посты", callback_data="show_my_posts")],
            [InlineKeyboardButton(text="📅 Контент-план", callback_data="show_content_plan")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="show_settings")]
        ])
        await message.answer("👋 <b>Бот для отложенного постинга</b>\n\n🤖 Добавьте меня в группу/канал!", reply_markup=keyboard, parse_mode=ParseMode.HTML)
    
    async def help_command(self, message: Message):
        await message.answer("<b>📖 Возможности:</b>\n\n• Отложенная публикация\n• Публикация сразу\n• Закрепление постов\n• Спойлер на медиа\n• Кнопка «Участвовать»\n• URL кнопки\n• Редактирование отправленных постов", parse_mode=ParseMode.HTML)
    
    async def set_timezone_command(self, message: Message):
        timezones = [("Asia/Jerusalem", "🇮🇱 Иерусалим"), ("Europe/Moscow", "🇷🇺 Москва"), ("Europe/Kiev", "🇺🇦 Киев"), ("UTC", "🌍 UTC")]
        keyboard = [[InlineKeyboardButton(text=name, callback_data=f"tz_{code}")] for code, name in timezones]
        await message.answer("🌍 <b>Выберите часовой пояс:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.HTML)
    
    async def stats_command(self, message: Message):
        stats = await self.db.get_user_statistics(message.from_user.id)
        if stats:
            _, _, created, sent, failed, _ = stats
        else:
            created, sent, failed = 0, 0, 0
        posts = await self.db.get_user_scheduled_posts(message.from_user.id)
        active = sum(1 for p in posts if p[10])
        await message.answer(f"📊 <b>Статистика</b>\n\n📝 Создано: <b>{created}</b>\n✅ Отправлено: <b>{sent}</b>\n❌ Ошибок: <b>{failed}</b>\n🔄 Активных: <b>{active}</b>", parse_mode=ParseMode.HTML)
    
    async def on_bot_added(self, event: ChatMemberUpdated):
        if event.new_chat_member.status == "administrator":
            chat = event.chat
            user = event.from_user
            await self.db.add_chat(chat.id, chat.title or "Без названия", chat.type, user.id)
            try:
                await self.bot.send_message(user.id, f"✅ Добавлен в <b>{chat.title}</b>!", parse_mode=ParseMode.HTML)
            except:
                pass
    
    async def my_chats_command(self, message: Message):
        chats = await self.db.get_user_chats(message.from_user.id)
        if not chats:
            await message.answer("❌ Нет подключенных чатов")
            return
        keyboard = []
        for c in chats:
            emoji = "📢" if c[2] == "channel" else "👥"
            keyboard.append([InlineKeyboardButton(text=f"{emoji} {c[1]}", callback_data=f"chat_info_{c[0]}")])
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
        await message.answer("📋 <b>Ваши чаты:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.HTML)
    
    async def new_post_command(self, message: Message, state: FSMContext):
        chats = await self.db.get_user_chats(message.from_user.id)
        if not chats:
            await message.answer("❌ Сначала добавьте бота в чат")
            return
        keyboard = []
        for c in chats:
            emoji = "📢" if c[2] == "channel" else "👥"
            keyboard.append([InlineKeyboardButton(text=f"{emoji} {c[1]}", callback_data=f"create_post_{c[0]}")])
        keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_post")])
        await message.answer("📝 <b>Выберите чат:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.HTML)
    
    async def my_posts_command(self, message: Message):
        posts = await self.db.get_user_scheduled_posts(message.from_user.id)
        if not posts:
            await message.answer("📋 Нет постов")
            return
        keyboard = []
        for p in posts[:10]:
            status = "✅" if p[10] else "❌"
            content = (p[3][:15] + "...") if p[3] and len(p[3]) > 15 else (p[3] or "Медиа")
            keyboard.append([InlineKeyboardButton(text=f"{status} #{p[0]}: {content}", callback_data=f"post_manage_{p[0]}")])
        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
        await message.answer("📋 <b>Ваши посты:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.HTML)
