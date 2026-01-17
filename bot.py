import asyncio
import logging
import calendar
import json
import secrets
from datetime import datetime
from typing import List, Optional
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated, BufferedInputFile
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
from aiohttp import web
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log', encoding='utf-8'), logging.StreamHandler()])
logger = logging.getLogger(__name__)

# ==================== DATABASE ====================
class Database:
    def __init__(self, path="scheduler.db"):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript('''
                CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, timezone TEXT DEFAULT 'Asia/Jerusalem', joined_date TEXT, web_token TEXT);
                CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, chat_title TEXT, chat_type TEXT, owner_id INTEGER, added_date TEXT);
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    post_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, owner_id INTEGER, content TEXT,
                    media_type TEXT, media_file_id TEXT, schedule_type TEXT, scheduled_time TEXT, scheduled_date TEXT,
                    days_of_week TEXT, is_active INTEGER DEFAULT 1, created_at TEXT, last_sent_at TEXT,
                    execution_count INTEGER DEFAULT 0, pin_post INTEGER DEFAULT 0, has_spoiler INTEGER DEFAULT 0,
                    has_participate_button INTEGER DEFAULT 0, button_text TEXT DEFAULT 'Участвовать',
                    url_buttons TEXT DEFAULT '[]', sent_message_id INTEGER, template_name TEXT
                );
                CREATE TABLE IF NOT EXISTS templates (
                    template_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, name TEXT,
                    content TEXT, media_type TEXT, media_file_id TEXT, pin_post INTEGER DEFAULT 0,
                    has_spoiler INTEGER DEFAULT 0, has_participate_button INTEGER DEFAULT 0,
                    button_text TEXT DEFAULT 'Участвовать', url_buttons TEXT DEFAULT '[]', created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS participants (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER, username TEXT, joined_at TEXT, UNIQUE(post_id, user_id));
                CREATE TABLE IF NOT EXISTS statistics (stat_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE, posts_created INTEGER DEFAULT 0, posts_sent INTEGER DEFAULT 0, posts_failed INTEGER DEFAULT 0, last_updated TEXT);
            ''')
            # Migrations
            for col in ['template_name TEXT', 'web_token TEXT']:
                try: await db.execute(f"ALTER TABLE {'scheduled_posts' if 'template' in col else 'users'} ADD COLUMN {col}")
                except: pass
            await db.commit()

    async def _exec(self, sql, params=()): 
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(sql, params)
            await db.commit()
            return cur

    async def _fetch(self, sql, params=()):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(sql, params)
            return await cur.fetchall()

    async def _one(self, sql, params=()):
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(sql, params)
            return await cur.fetchone()

    # Users
    async def add_user(self, uid, uname):
        token = secrets.token_urlsafe(32)
        await self._exec("INSERT OR IGNORE INTO users (user_id, username, joined_date, web_token) VALUES (?,?,?,?)", (uid, uname, datetime.now().isoformat(), token))
        await self._exec("INSERT OR IGNORE INTO statistics (user_id, last_updated) VALUES (?,?)", (uid, datetime.now().isoformat()))
        return token

    async def get_user_token(self, uid): return (await self._one("SELECT web_token FROM users WHERE user_id=?", (uid,)) or (None,))[0]
    async def get_user_by_token(self, token): return await self._one("SELECT user_id FROM users WHERE web_token=?", (token,))
    async def get_tz(self, uid): return (await self._one("SELECT timezone FROM users WHERE user_id=?", (uid,)) or ('Asia/Jerusalem',))[0]
    async def set_tz(self, uid, tz): await self._exec("UPDATE users SET timezone=? WHERE user_id=?", (tz, uid))

    # Chats
    async def add_chat(self, cid, title, ctype, owner):
        await self._exec("INSERT OR REPLACE INTO chats VALUES (?,?,?,?,?)", (cid, title, ctype, owner, datetime.now().isoformat()))
    async def get_chats(self, uid): return await self._fetch("SELECT * FROM chats WHERE owner_id=?", (uid,))

    # Posts
    async def add_post(self, **kw) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                '''INSERT INTO scheduled_posts (chat_id, owner_id, content, media_type, media_file_id, schedule_type, 
                   scheduled_time, scheduled_date, days_of_week, created_at, pin_post, has_spoiler, 
                   has_participate_button, button_text, url_buttons, template_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (kw['chat_id'], kw['owner_id'], kw.get('content',''), kw.get('media_type'), kw.get('media_file_id'),
                 kw.get('schedule_type'), kw.get('scheduled_time',''), kw.get('scheduled_date'), kw.get('days_of_week'),
                 datetime.now().isoformat(), kw.get('pin_post',0), kw.get('has_spoiler',0),
                 kw.get('has_participate',0), kw.get('button_text','Участвовать'), kw.get('url_buttons','[]'), kw.get('template_name')))
            await db.commit()
            return cur.lastrowid

    async def get_post(self, pid): return await self._one("SELECT * FROM scheduled_posts WHERE post_id=?", (pid,))
    async def get_posts(self, uid): return await self._fetch("SELECT * FROM scheduled_posts WHERE owner_id=? ORDER BY created_at DESC", (uid,))
    async def update_post(self, pid, **kw):
        if kw: await self._exec(f"UPDATE scheduled_posts SET {','.join(f'{k}=?' for k in kw)} WHERE post_id=?", (*kw.values(), pid))
    async def delete_post(self, pid): await self._exec("DELETE FROM scheduled_posts WHERE post_id=?", (pid,))
    async def get_active_posts(self): return await self._fetch("SELECT post_id FROM scheduled_posts WHERE is_active=1 AND schedule_type!='instant'")

    # Templates
    async def add_template(self, owner_id, name, content, media_type=None, media_file_id=None, pin=0, spoiler=0, participate=0, btn_text='Участвовать', url_btns='[]'):
        await self._exec('''INSERT INTO templates (owner_id, name, content, media_type, media_file_id, pin_post, has_spoiler, 
                            has_participate_button, button_text, url_buttons, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                         (owner_id, name, content, media_type, media_file_id, pin, spoiler, participate, btn_text, url_btns, datetime.now().isoformat()))
    async def get_templates(self, uid): return await self._fetch("SELECT * FROM templates WHERE owner_id=?", (uid,))
    async def get_template(self, tid): return await self._one("SELECT * FROM templates WHERE template_id=?", (tid,))
    async def delete_template(self, tid): await self._exec("DELETE FROM templates WHERE template_id=?", (tid,))

    # Stats
    async def get_stats(self, uid): return await self._one("SELECT * FROM statistics WHERE user_id=?", (uid,))
    async def update_stats(self, uid, created=0, sent=0, failed=0):
        await self._exec("UPDATE statistics SET posts_created=posts_created+?, posts_sent=posts_sent+?, posts_failed=posts_failed+?, last_updated=? WHERE user_id=?",
                         (created, sent, failed, datetime.now().isoformat(), uid))

    # Participants
    async def add_participant(self, pid, uid, uname):
        try:
            await self._exec("INSERT INTO participants VALUES (NULL,?,?,?,?)", (pid, uid, uname, datetime.now().isoformat()))
            return True
        except: return False
    async def count_participants(self, pid): return (await self._one("SELECT COUNT(*) FROM participants WHERE post_id=?", (pid,)) or (0,))[0]

    # Export/Import
    async def export_posts(self, uid):
        posts = await self.get_posts(uid)
        return [{"content": p[3], "media_type": p[4], "schedule_type": p[6], "scheduled_time": p[7], 
                 "scheduled_date": p[8], "days_of_week": p[9], "pin_post": p[14], "has_spoiler": p[15],
                 "has_participate": p[16], "button_text": p[17], "url_buttons": p[18]} for p in posts]

# ==================== STATES ====================
class S(StatesGroup):
    content = State(); media = State(); time = State(); url_btn = State(); config = State()
    edit_content = State(); add_media = State(); edit_url = State()
    template_name = State(); template_content = State(); import_file = State()

# ==================== KEYBOARDS ====================
def kb(rows): return InlineKeyboardMarkup(inline_keyboard=rows)
def btn(text, cb): return InlineKeyboardButton(text=text, callback_data=cb)
def url_btn(text, url): return InlineKeyboardButton(text=text, url=url)

def main_kb():
    rows = [[btn("📋 Чаты", "chats")], [btn("📝 Создать пост", "new_post")], [btn("📊 Посты", "posts")],
            [btn("📅 Контент-план", "plan")], [btn("📑 Шаблоны", "templates")],
            [btn("📤 Экспорт / 📥 Импорт", "export_import")]]
    if os.getenv("WEB_PORT"): rows.append([btn("🌐 Веб-панель", "web_panel")])
    rows.append([btn("⚙️ Настройки", "settings")])
    return kb(rows)

def back_btn(cb="main"): return [btn("🔙 Назад", cb)]

def schedule_kb():
    return kb([[btn("🚀 Сейчас", "now")],
               [btn("⏰ Один раз", "sched_once"), btn("🔄 Ежедневно", "sched_daily")],
               [btn("📅 Еженедельно", "sched_weekly"), btn("🗓 Ежемесячно", "sched_monthly")],
               [btn("❌ Отмена", "cancel")]])

def settings_kb(data):
    pin, spoiler, part = data.get("pin_post"), data.get("has_spoiler"), data.get("has_participate")
    media = data.get("content_type") in ("photo", "video") or data.get("media_file_id")
    rows = [[btn(f"{'✅' if pin else '⬜'} Закрепить", "toggle_pin")]]
    if media: rows.append([btn(f"{'✅' if spoiler else '⬜'} Спойлер", "toggle_spoiler")])
    rows.append([btn(f"{'✅' if part else '⬜'} Участвовать", "toggle_participate")])
    rows.append([btn(f"🔗 URL кнопки ({len(data.get('url_buttons',[]))})", "url_buttons")])
    if not media: rows.append([btn("🖼 Добавить медиа", "add_media")])
    rows.append([btn("📑 Из шаблона", "from_template")])
    rows.append([btn("👁 Превью", "preview"), btn("✅ Сохранить", "save")])
    rows.append([btn("🚀 Опубликовать", "publish"), btn("💾 Как шаблон", "save_template")])
    rows.append([btn("❌ Отмена", "cancel")])
    return kb(rows)

def post_kb(pid, has_part, btn_text, url_btns, count):
    rows = [[url_btn(b["text"], b["url"])] for b in url_btns if b.get("text") and b.get("url")]
    if has_part: rows.append([btn(f"{btn_text} ({count})", f"part_{pid}")])
    return kb(rows) if rows else None

# ==================== WEB SERVER ====================
class WebPanel:
    def __init__(self, db: Database, bot_instance):
        self.db = db
        self.bot = bot_instance
        self.app = web.Application()
        self.app.router.add_get('/', self.index)
        self.app.router.add_get('/api/posts', self.get_posts)
        self.app.router.add_get('/api/export', self.export_posts)
        self.app.router.add_post('/api/import', self.import_posts)
        self.app.router.add_delete('/api/posts/{pid}', self.delete_post)

    async def index(self, req):
        token = req.query.get('token')
        if not token:
            return web.Response(text="Token required", status=401)
        user = await self.db.get_user_by_token(token)
        if not user:
            return web.Response(text="Invalid token", status=401)
        html = '''<!DOCTYPE html><html><head><meta charset="utf-8"><title>PostBot Panel</title>
        <style>
            * { box-sizing: border-box; margin: 0; padding: 0; }
            body { font-family: 'Segoe UI', sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; color: #eee; padding: 20px; }
            .container { max-width: 900px; margin: 0 auto; }
            h1 { text-align: center; margin-bottom: 30px; color: #00d4ff; text-shadow: 0 0 20px rgba(0,212,255,0.3); }
            .card { background: rgba(255,255,255,0.05); border-radius: 15px; padding: 20px; margin-bottom: 15px; border: 1px solid rgba(255,255,255,0.1); backdrop-filter: blur(10px); }
            .post { display: flex; justify-content: space-between; align-items: center; }
            .post-content { flex: 1; }
            .post-meta { font-size: 12px; color: #888; margin-top: 5px; }
            .btn { padding: 8px 16px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; transition: all 0.3s; }
            .btn-danger { background: linear-gradient(135deg, #ff416c, #ff4b2b); color: white; }
            .btn-primary { background: linear-gradient(135deg, #00d4ff, #0099ff); color: white; }
            .btn:hover { transform: translateY(-2px); box-shadow: 0 5px 20px rgba(0,0,0,0.3); }
            .actions { display: flex; gap: 10px; margin-bottom: 20px; }
            .status { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 11px; }
            .status.active { background: #00c853; } .status.inactive { background: #ff5252; }
            #posts { display: grid; gap: 15px; }
        </style></head><body>
        <div class="container">
            <h1>🤖 PostBot Panel</h1>
            <div class="actions">
                <button class="btn btn-primary" onclick="exportPosts()">📤 Экспорт JSON</button>
                <input type="file" id="importFile" accept=".json" style="display:none" onchange="importPosts(this)">
                <button class="btn btn-primary" onclick="document.getElementById('importFile').click()">📥 Импорт JSON</button>
            </div>
            <div id="posts"></div>
        </div>
        <script>
            const token = new URLSearchParams(location.search).get('token');
            async function load() {
                const res = await fetch('/api/posts?token=' + token);
                const posts = await res.json();
                document.getElementById('posts').innerHTML = posts.map(p => `
                    <div class="card post">
                        <div class="post-content">
                            <span class="status ${p.is_active ? 'active' : 'inactive'}">${p.is_active ? 'Активен' : 'Откл'}</span>
                            <strong> #${p.post_id}</strong>: ${(p.content || 'Медиа').substring(0, 100)}...
                            <div class="post-meta">${p.schedule_type} | ${p.scheduled_time || ''} ${p.scheduled_date || ''}</div>
                        </div>
                        <button class="btn btn-danger" onclick="del(${p.post_id})">🗑</button>
                    </div>
                `).join('');
            }
            async function del(pid) {
                if (confirm('Удалить пост #' + pid + '?')) {
                    await fetch('/api/posts/' + pid + '?token=' + token, {method: 'DELETE'});
                    load();
                }
            }
            async function exportPosts() {
                const res = await fetch('/api/export?token=' + token);
                const data = await res.json();
                const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
                const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
                a.download = 'posts_export.json'; a.click();
            }
            async function importPosts(input) {
                const file = input.files[0]; if (!file) return;
                const text = await file.text();
                await fetch('/api/import?token=' + token, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: text});
                alert('Импортировано!'); load();
            }
            load();
        </script></body></html>'''
        return web.Response(text=html, content_type='text/html')

    async def get_posts(self, req):
        token = req.query.get('token')
        user = await self.db.get_user_by_token(token)
        if not user: return web.json_response([], status=401)
        posts = await self.db.get_posts(user[0])
        return web.json_response([{"post_id": p[0], "content": p[3], "is_active": p[10], "schedule_type": p[6], 
                                   "scheduled_time": p[7], "scheduled_date": p[8]} for p in posts])

    async def export_posts(self, req):
        token = req.query.get('token')
        user = await self.db.get_user_by_token(token)
        if not user: return web.json_response({"error": "unauthorized"}, status=401)
        data = await self.db.export_posts(user[0])
        return web.json_response(data)

    async def import_posts(self, req):
        token = req.query.get('token')
        user = await self.db.get_user_by_token(token)
        if not user: return web.json_response({"error": "unauthorized"}, status=401)
        data = await req.json()
        chats = await self.db.get_chats(user[0])
        if not chats: return web.json_response({"error": "no chats"}, status=400)
        chat_id = chats[0][0]
        for p in data:
            await self.db.add_post(chat_id=chat_id, owner_id=user[0], content=p.get('content',''), 
                                   media_type=p.get('media_type'), schedule_type=p.get('schedule_type','instant'),
                                   scheduled_time=p.get('scheduled_time',''), scheduled_date=p.get('scheduled_date'),
                                   days_of_week=p.get('days_of_week'), pin_post=p.get('pin_post',0),
                                   has_spoiler=p.get('has_spoiler',0), has_participate=p.get('has_participate',0),
                                   button_text=p.get('button_text','Участвовать'), url_buttons=p.get('url_buttons','[]'))
        return web.json_response({"imported": len(data)})

    async def delete_post(self, req):
        token = req.query.get('token')
        user = await self.db.get_user_by_token(token)
        if not user: return web.json_response({"error": "unauthorized"}, status=401)
        pid = int(req.match_info['pid'])
        await self.db.delete_post(pid)
        return web.json_response({"deleted": pid})

# ==================== BOT ====================
class SchedulerBot:
    def __init__(self, token):
        self.bot = Bot(token=token)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.db = Database()
        self.router = Router()
        self.dp.include_router(self.router)
        self.scheduler = AsyncIOScheduler()
        self.web = WebPanel(self.db, self.bot)
        self._register()

    def _register(self):
        r = self.router
        r.message.register(self.cmd_start, Command("start"), F.chat.type == ChatType.PRIVATE)
        r.message.register(self.cmd_help, Command("help"), F.chat.type == ChatType.PRIVATE)
        r.my_chat_member.register(self.on_added)
        r.message.register(self.on_content, S.content, F.chat.type == ChatType.PRIVATE)
        r.message.register(self.on_media, S.media, F.chat.type == ChatType.PRIVATE)
        r.message.register(self.on_time, S.time, F.chat.type == ChatType.PRIVATE)
        r.message.register(self.on_url_btn, S.url_btn, F.chat.type == ChatType.PRIVATE)
        r.message.register(self.on_template_name, S.template_name, F.chat.type == ChatType.PRIVATE)
        r.message.register(self.on_template_content, S.template_content, F.chat.type == ChatType.PRIVATE)
        r.message.register(self.on_import_file, S.import_file, F.chat.type == ChatType.PRIVATE)
        r.callback_query.register(self.on_callback)

    async def safe_edit(self, msg, text=None, markup=None):
        try:
            if text: return await msg.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            return await msg.edit_reply_markup(reply_markup=markup)
        except TelegramBadRequest: pass

    # Commands
    async def cmd_start(self, msg: Message):
        await self.db.add_user(msg.from_user.id, msg.from_user.username)
        await msg.answer("👋 <b>PostBot</b> — отложенный постинг\n\n🤖 Добавьте меня в группу/канал как админа!", reply_markup=main_kb(), parse_mode=ParseMode.HTML)

    async def cmd_help(self, msg: Message):
        await msg.answer("<b>📖 Возможности:</b>\n\n• Отложенные публикации\n• Шаблоны постов\n• Веб-панель управления\n• Экспорт/импорт в JSON\n• Превью перед отправкой\n• Кнопки URL и «Участвовать»", parse_mode=ParseMode.HTML)

    async def on_added(self, ev: ChatMemberUpdated):
        if ev.new_chat_member.status == "administrator":
            await self.db.add_chat(ev.chat.id, ev.chat.title or "Без названия", ev.chat.type, ev.from_user.id)
            try: await self.bot.send_message(ev.from_user.id, f"✅ Добавлен в <b>{ev.chat.title}</b>!", parse_mode=ParseMode.HTML)
            except: pass

    # Callback handler
    async def on_callback(self, cb: CallbackQuery, state: FSMContext):
        d, uid = cb.data, cb.from_user.id
        
        if d == "main":
            await state.clear()
            await self.safe_edit(cb.message, "👋 <b>Главное меню</b>", main_kb())
        elif d == "chats":
            chats = await self.db.get_chats(uid)
            if not chats: return await cb.answer("Нет чатов", show_alert=True)
            rows = [[btn(f"{'📢' if c[2]=='channel' else '👥'} {c[1]}", f"info_{c[0]}")] for c in chats] + [back_btn()]
            await self.safe_edit(cb.message, "📋 <b>Чаты:</b>", kb(rows))
        elif d == "new_post":
            chats = await self.db.get_chats(uid)
            if not chats: return await cb.answer("Добавьте бота в чат", show_alert=True)
            rows = [[btn(f"{'📢' if c[2]=='channel' else '👥'} {c[1]}", f"chat_{c[0]}")] for c in chats] + [back_btn()]
            await self.safe_edit(cb.message, "📝 <b>Выберите чат:</b>", kb(rows))
        elif d == "posts":
            posts = await self.db.get_posts(uid)
            if not posts: return await cb.answer("Нет постов", show_alert=True)
            rows = [[btn(f"{'✅' if p[10] else '❌'} #{p[0]}: {(p[3] or 'Медиа')[:20]}", f"post_{p[0]}")] for p in posts[:15]] + [back_btn()]
            await self.safe_edit(cb.message, "📊 <b>Посты:</b>", kb(rows))
        elif d == "plan":
            posts = [p for p in await self.db.get_posts(uid) if p[10] and p[6] != "instant"]
            if not posts: return await cb.answer("Нет запланированных", show_alert=True)
            text = "📅 <b>Контент-план</b>\n\n"
            for p in posts[:15]:
                text += f"{'📌' if p[6]=='once' else '🔄'} <b>{p[8] or ''} {p[7]}</b>\n└ #{p[0]}: {(p[3] or 'Медиа')[:30]}\n\n"
            await self.safe_edit(cb.message, text, kb([back_btn()]))
        elif d == "templates":
            tpls = await self.db.get_templates(uid)
            rows = [[btn(f"📑 {t[2]}", f"tpl_{t[0]}")] for t in tpls] + [[btn("➕ Создать шаблон", "new_template")]] + [back_btn()]
            await self.safe_edit(cb.message, "📑 <b>Шаблоны:</b>", kb(rows))
        elif d == "new_template":
            await self.safe_edit(cb.message, "📑 <b>Введите название шаблона:</b>")
            await state.set_state(S.template_name)
        elif d.startswith("tpl_"):
            tid = int(d.split("_")[1])
            tpl = await self.db.get_template(tid)
            if not tpl: return await cb.answer("Не найден", show_alert=True)
            text = f"📑 <b>{tpl[2]}</b>\n\n{(tpl[3] or 'Медиа')[:200]}"
            await self.safe_edit(cb.message, text, kb([[btn("📝 Использовать", f"use_tpl_{tid}")], [btn("🗑 Удалить", f"del_tpl_{tid}")], back_btn("templates")]))
        elif d.startswith("use_tpl_"):
            tid = int(d.split("_")[2])
            tpl = await self.db.get_template(tid)
            chats = await self.db.get_chats(uid)
            if not chats: return await cb.answer("Нет чатов", show_alert=True)
            await state.update_data(content=tpl[3], media_type=tpl[4], media_file_id=tpl[5], pin_post=tpl[6],
                                    has_spoiler=tpl[7], has_participate=tpl[8], button_text=tpl[9],
                                    url_buttons=json.loads(tpl[10]) if tpl[10] else [], template_name=tpl[2])
            rows = [[btn(f"{'📢' if c[2]=='channel' else '👥'} {c[1]}", f"chat_{c[0]}")] for c in chats] + [back_btn()]
            await self.safe_edit(cb.message, f"📝 Шаблон «{tpl[2]}»\n\n<b>Выберите чат:</b>", kb(rows))
        elif d.startswith("del_tpl_"):
            await self.db.delete_template(int(d.split("_")[2]))
            await cb.answer("🗑 Удалён", show_alert=True)
            tpls = await self.db.get_templates(uid)
            rows = [[btn(f"📑 {t[2]}", f"tpl_{t[0]}")] for t in tpls] + [[btn("➕ Создать", "new_template")]] + [back_btn()]
            await self.safe_edit(cb.message, "📑 <b>Шаблоны:</b>", kb(rows))
        elif d == "export_import":
            await self.safe_edit(cb.message, "📤📥 <b>Экспорт / Импорт</b>\n\nВыберите действие:", kb([
                [btn("📤 Экспорт в JSON", "export")], [btn("📥 Импорт из JSON", "import")], back_btn()
            ]))
        elif d == "export":
            data = await self.db.export_posts(uid)
            if not data: return await cb.answer("Нет постов", show_alert=True)
            file = BufferedInputFile(json.dumps(data, ensure_ascii=False, indent=2).encode(), filename="posts_export.json")
            await self.bot.send_document(uid, file, caption="📤 Экспорт постов")
            await cb.answer()
        elif d == "import":
            await self.safe_edit(cb.message, "📥 <b>Отправьте JSON файл с постами:</b>")
            await state.set_state(S.import_file)
        elif d == "web_panel":
            token = await self.db.get_user_token(uid)
            port = os.getenv("WEB_PORT", "8080")
            host = os.getenv("WEB_HOST", "localhost")
            url = f"http://{host}:{port}/?token={token}"
            await self.safe_edit(cb.message, f"🌐 <b>Веб-панель</b>\n\n<a href='{url}'>Открыть панель</a>\n\n⚠️ Не делитесь ссылкой!", kb([back_btn()]))
        elif d == "settings":
            tz = await self.db.get_tz(uid)
            await self.safe_edit(cb.message, "⚙️ <b>Настройки</b>", kb([[btn(f"🌍 Часовой пояс: {tz}", "change_tz")], back_btn()]))
        elif d == "change_tz":
            tzs = [("Asia/Jerusalem", "🇮🇱"), ("Europe/Moscow", "🇷🇺"), ("UTC", "🌍")]
            await self.safe_edit(cb.message, "🌍 <b>Часовой пояс:</b>", kb([[btn(f"{e} {t}", f"tz_{t}")] for t, e in tzs] + [back_btn("settings")]))
        elif d.startswith("tz_"):
            await self.db.set_tz(uid, d[3:])
            await cb.answer(f"✅ {d[3:]}", show_alert=True)
        elif d.startswith("chat_"):
            cid = int(d.split("_")[1])
            data = await state.get_data()
            await state.update_data(chat_id=cid, pin_post=data.get('pin_post',0), has_spoiler=data.get('has_spoiler',0), 
                                    has_participate=data.get('has_participate',0), button_text=data.get('button_text','Участвовать'), 
                                    url_buttons=data.get('url_buttons',[]))
            if data.get('content') or data.get('media_file_id'):  # From template
                await self.safe_edit(cb.message, "⏱ <b>Что сделать?</b>", schedule_kb())
            else:
                await self.safe_edit(cb.message, "📋 <b>Тип:</b>", kb([
                    [btn("📝 Текст", "type_text"), btn("🖼 Фото", "type_photo")],
                    [btn("🎥 Видео", "type_video"), btn("📎 Документ", "type_doc")],
                    [btn("❌ Отмена", "cancel")]
                ]))
        elif d.startswith("type_"):
            t = d.split("_")[1]
            await state.update_data(content_type=t)
            if t == "text":
                await self.safe_edit(cb.message, "✍️ <b>Введите текст:</b>")
                await state.set_state(S.content)
            else:
                await self.safe_edit(cb.message, f"📎 <b>Отправьте {t}:</b>")
                await state.set_state(S.media)
        elif d.startswith("sched_"):
            st = d.split("_")[1]
            await state.update_data(schedule_type=st, selected_times=[])
            if st == "once":
                now = datetime.now()
                await self.safe_edit(cb.message, "📅 <b>Дата:</b>", self._calendar(now.year, now.month))
            elif st == "daily":
                await self.safe_edit(cb.message, "⏰ <b>Время:</b>\n💡 Можно несколько!", self._time_picker(True))
                await state.update_data(multi_time=True, next_step="config")
            else:
                await self.safe_edit(cb.message, "⏰ <b>Время:</b>", self._time_picker())
                await state.update_data(next_step="days" if st == "weekly" else "config")
        elif d.startswith("cal_"):
            await self._handle_calendar(cb, state, d)
        elif d.startswith("time_") and d != "time_manual":
            await self._handle_time(cb, state, d)
        elif d == "times_done":
            data = await state.get_data()
            times = data.get("selected_times", [])
            if not times: return await cb.answer("Выберите время", show_alert=True)
            await state.update_data(scheduled_time=",".join(times), multi_time=False)
            await self._show_settings(cb.message, state)
        elif d == "time_manual":
            await self.safe_edit(cb.message, "⏰ <b>Введите время (HH:MM):</b>")
            await state.set_state(S.time)
        elif d.startswith("day_"):
            await self._handle_day(cb, state, int(d.split("_")[2]))
        elif d == "days_done":
            data = await state.get_data()
            sel = data.get("selected_days", [])
            if not sel: return await cb.answer("Выберите дни", show_alert=True)
            await state.update_data(days_of_week=",".join(map(str, sorted(sel))))
            await self._show_settings(cb.message, state)
        elif d == "now":
            await self._publish(cb.message, state, uid, False)
        elif d in ("toggle_pin", "toggle_spoiler", "toggle_participate"):
            key = d.replace("toggle_", "")
            if key == "participate": key = "has_participate"
            elif key == "pin": key = "pin_post"
            elif key == "spoiler": key = "has_spoiler"
            data = await state.get_data()
            await state.update_data(**{key: not data.get(key, False)})
            await self.safe_edit(cb.message, None, settings_kb(await state.get_data()))
        elif d == "url_buttons":
            data = await state.get_data()
            btns = data.get("url_buttons", [])
            rows = [[btn(f"🗑 {b['text']}", f"rm_url_{i}")] for i, b in enumerate(btns)]
            rows += [[btn("➕ Добавить", "add_url")], back_btn("back_settings")]
            await self.safe_edit(cb.message, "🔗 <b>URL кнопки:</b>", kb(rows))
        elif d == "add_url":
            await self.safe_edit(cb.message, "🔗 <b>Формат:</b>\n<code>Текст | https://url</code>")
            await state.set_state(S.url_btn)
        elif d.startswith("rm_url_"):
            i = int(d.split("_")[2])
            data = await state.get_data()
            btns = data.get("url_buttons", [])
            if 0 <= i < len(btns): btns.pop(i)
            await state.update_data(url_buttons=btns)
            rows = [[btn(f"🗑 {b['text']}", f"rm_url_{j}")] for j, b in enumerate(btns)]
            rows += [[btn("➕ Добавить", "add_url")], back_btn("back_settings")]
            await self.safe_edit(cb.message, None, kb(rows))
        elif d == "back_settings":
            await self._show_settings(cb.message, state)
        elif d == "add_media":
            await self.safe_edit(cb.message, "🖼 <b>Отправьте фото/видео:</b>")
            await state.set_state(S.add_media)
        elif d == "from_template":
            tpls = await self.db.get_templates(uid)
            if not tpls: return await cb.answer("Нет шаблонов", show_alert=True)
            rows = [[btn(f"📑 {t[2]}", f"apply_tpl_{t[0]}")] for t in tpls] + [back_btn("back_settings")]
            await self.safe_edit(cb.message, "📑 <b>Выберите шаблон:</b>", kb(rows))
        elif d.startswith("apply_tpl_"):
            tid = int(d.split("_")[2])
            tpl = await self.db.get_template(tid)
            if not tpl: return await cb.answer("Не найден", show_alert=True)
            data = await state.get_data()
            await state.update_data(content=tpl[3], media_type=tpl[4], media_file_id=tpl[5], content_type=tpl[4] or 'text',
                                    pin_post=tpl[6], has_spoiler=tpl[7], has_participate=tpl[8], button_text=tpl[9],
                                    url_buttons=json.loads(tpl[10]) if tpl[10] else [])
            await cb.answer(f"✅ Шаблон «{tpl[2]}» применён")
            await self._show_settings(cb.message, state)
        elif d == "preview":
            await self._send_preview(uid, state)
            await cb.answer()
        elif d == "save":
            await self._save_post(cb.message, state, uid)
            await cb.answer("✅ Сохранено!")
        elif d == "publish":
            await self._publish(cb.message, state, uid, True)
            await cb.answer("🚀 Публикация...")
        elif d == "save_template":
            await self.safe_edit(cb.message, "💾 <b>Название шаблона:</b>")
            await state.set_state(S.template_name)
        elif d == "cancel":
            await state.clear()
            await self.safe_edit(cb.message, "❌ <b>Отменено</b>", kb([[btn("📝 Новый пост", "new_post")], back_btn()]))
        elif d.startswith("post_"):
            pid = int(d.split("_")[1])
            post = await self.db.get_post(pid)
            if not post: return await cb.answer("Не найден", show_alert=True)
            info = f"📋 <b>Пост #{pid}</b>\n\n{'✅ Активен' if post[10] else '❌ Откл'}\n📝 {post[6]} | {post[7]} {post[8] or ''}\n\n{(post[3] or 'Медиа')[:200]}"
            await self.safe_edit(cb.message, info, kb([
                [btn("👁 Превью", f"view_{pid}")],
                [btn("❌ Откл" if post[10] else "✅ Вкл", f"toggle_{pid}")],
                [btn("🗑 Удалить", f"del_{pid}")],
                back_btn("posts")
            ]))
        elif d.startswith("view_"):
            pid = int(d.split("_")[1])
            post = await self.db.get_post(pid)
            if post: await self._send_post_preview(uid, post)
            await cb.answer()
        elif d.startswith("toggle_") and d.count("_") == 1:
            pid = int(d.split("_")[1])
            post = await self.db.get_post(pid)
            if post:
                new = 0 if post[10] else 1
                await self.db.update_post(pid, is_active=new)
                if new: await self._register_job(pid)
                else:
                    try: self.scheduler.remove_job(f"post_{pid}")
                    except: pass
                await cb.answer("✅ Вкл" if new else "❌ Откл")
        elif d.startswith("del_") and not d.startswith("del_tpl"):
            pid = int(d.split("_")[1])
            await self.db.delete_post(pid)
            try: self.scheduler.remove_job(f"post_{pid}")
            except: pass
            await cb.answer("🗑 Удалён", show_alert=True)
            posts = await self.db.get_posts(uid)
            rows = [[btn(f"{'✅' if p[10] else '❌'} #{p[0]}: {(p[3] or 'Медиа')[:20]}", f"post_{p[0]}")] for p in posts[:15]] + [back_btn()]
            await self.safe_edit(cb.message, "📊 <b>Посты:</b>", kb(rows))
        elif d.startswith("part_"):
            pid = int(d.split("_")[1])
            added = await self.db.add_participant(pid, uid, cb.from_user.username or cb.from_user.first_name)
            count = await self.db.count_participants(pid)
            await cb.answer(f"✅ Участвуете! Всего: {count}" if added else "Вы уже участвуете!", show_alert=True)
            post = await self.db.get_post(pid)
            if post:
                markup = post_kb(pid, post[16], post[17], json.loads(post[18]) if post[18] else [], count)
                try: await self.safe_edit(cb.message, None, markup)
                except: pass
        else:
            await cb.answer()

    # Message handlers
    async def on_content(self, msg: Message, state: FSMContext):
        await state.update_data(content=msg.text or msg.caption or "")
        await msg.answer("⏱ <b>Что сделать?</b>", reply_markup=schedule_kb(), parse_mode=ParseMode.HTML)

    async def on_media(self, msg: Message, state: FSMContext):
        fid, mt = None, None
        if msg.photo: fid, mt = msg.photo[-1].file_id, "photo"
        elif msg.video: fid, mt = msg.video.file_id, "video"
        elif msg.document: fid, mt = msg.document.file_id, "document"
        if not fid: return await msg.answer("❌ Отправьте медиа")
        await state.update_data(media_file_id=fid, content_type=mt, media_type=mt)
        await msg.answer("✍️ <b>Подпись:</b>", reply_markup=kb([[btn("⏭ Пропустить", "skip_caption")], [btn("❌ Отмена", "cancel")]]), parse_mode=ParseMode.HTML)
        await state.set_state(S.content)

    async def on_time(self, msg: Message, state: FSMContext):
        data = await state.get_data()
        times = []
        for line in msg.text.strip().split("\n"):
            line = line.strip()
            if not line: continue
            try:
                h, m = map(int, line.split(":"))
                if not (0 <= h <= 23 and 0 <= m <= 59): raise ValueError
                times.append(f"{h:02d}:{m:02d}")
            except: return await msg.answer(f"❌ Ошибка: {line}")
        if not times: return await msg.answer("❌ Формат: HH:MM")
        await state.update_data(scheduled_time=",".join(times), multi_time=False)
        if data.get("next_step") == "days":
            await state.update_data(selected_days=[])
            await msg.answer(f"⏰ {times[0]}\n\n📅 <b>Дни:</b>", reply_markup=self._days_picker([]), parse_mode=ParseMode.HTML)
        else:
            sent = await msg.answer("⏳")
            await self._show_settings(sent, state)

    async def on_url_btn(self, msg: Message, state: FSMContext):
        try:
            t, u = [p.strip() for p in msg.text.split("|")]
            if not t or not u.startswith("http"): raise ValueError
        except: return await msg.answer("❌ Формат: Текст | https://url")
        data = await state.get_data()
        btns = data.get("url_buttons", [])
        btns.append({"text": t, "url": u})
        await state.update_data(url_buttons=btns)
        sent = await msg.answer("✅ Добавлено")
        await self._show_settings(sent, state)

    async def on_template_name(self, msg: Message, state: FSMContext):
        name = msg.text.strip()
        data = await state.get_data()
        if data.get("content") or data.get("media_file_id"):  # Saving current post as template
            await self.db.add_template(msg.from_user.id, name, data.get("content"), data.get("media_type"),
                                       data.get("media_file_id"), data.get("pin_post",0), data.get("has_spoiler",0),
                                       data.get("has_participate",0), data.get("button_text","Участвовать"),
                                       json.dumps(data.get("url_buttons",[])))
            await msg.answer(f"💾 Шаблон «{name}» сохранён!", reply_markup=main_kb(), parse_mode=ParseMode.HTML)
            await state.clear()
        else:  # Creating new template - ask for content
            await state.update_data(template_name=name)
            await msg.answer("📝 <b>Введите текст шаблона:</b>", parse_mode=ParseMode.HTML)
            await state.set_state(S.template_content)

    async def on_template_content(self, msg: Message, state: FSMContext):
        data = await state.get_data()
        name = data.get("template_name", "Без имени")
        content = msg.text or ""
        await self.db.add_template(msg.from_user.id, name, content)
        await msg.answer(f"💾 Шаблон «{name}» сохранён!", reply_markup=main_kb(), parse_mode=ParseMode.HTML)
        await state.clear()

    async def on_import_file(self, msg: Message, state: FSMContext):
        if not msg.document: return await msg.answer("❌ Отправьте JSON файл")
        file = await self.bot.get_file(msg.document.file_id)
        data = await self.bot.download_file(file.file_path)
        try:
            posts = json.loads(data.read().decode())
        except: return await msg.answer("❌ Неверный JSON")
        chats = await self.db.get_chats(msg.from_user.id)
        if not chats: return await msg.answer("❌ Сначала добавьте бота в чат")
        cid = chats[0][0]
        count = 0
        for p in posts:
            await self.db.add_post(chat_id=cid, owner_id=msg.from_user.id, content=p.get('content',''),
                                   media_type=p.get('media_type'), schedule_type=p.get('schedule_type','instant'),
                                   scheduled_time=p.get('scheduled_time',''), scheduled_date=p.get('scheduled_date'),
                                   days_of_week=p.get('days_of_week'), pin_post=p.get('pin_post',0),
                                   has_spoiler=p.get('has_spoiler',0), has_participate=p.get('has_participate',0),
                                   button_text=p.get('button_text','Участвовать'), url_buttons=json.dumps(p.get('url_buttons',[])))
            count += 1
        await msg.answer(f"✅ Импортировано: {count} постов", reply_markup=main_kb())
        await state.clear()

    # Helpers
    def _calendar(self, y, m):
        names = ["", "Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
        rows = [[btn("◀️", f"cal_prev_{y}_{m}"), btn(f"{names[m]} {y}", "x"), btn("▶️", f"cal_next_{y}_{m}")]]
        rows.append([btn(d, "x") for d in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]])
        today = datetime.now().date()
        for week in calendar.monthcalendar(y, m):
            row = []
            for day in week:
                if day == 0: row.append(btn(" ", "x"))
                elif datetime(y, m, day).date() < today: row.append(btn("·", "x"))
                else: row.append(btn(str(day), f"cal_day_{y}_{m}_{day}"))
            rows.append(row)
        rows.append([btn("❌ Отмена", "cancel")])
        return kb(rows)

    def _time_picker(self, multi=False, sel=None):
        sel = sel or []
        hours = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
        rows = []
        for i in range(0, len(hours), 4):
            rows.append([btn(f"{'✅ ' if f'{h:02d}:00' in sel else ''}{h:02d}:00", f"time_{h:02d}_00") for h in hours[i:i+4]])
        rows.append([btn("⌨️ Вручную", "time_manual")])
        if multi and sel: rows.append([btn(f"✅ Готово ({len(sel)})", "times_done")])
        rows.append([btn("❌ Отмена", "cancel")])
        return kb(rows)

    def _days_picker(self, sel):
        names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        r1 = [btn(f"{'✅' if i in sel else ''}{names[i]}", f"day_toggle_{i}") for i in range(4)]
        r2 = [btn(f"{'✅' if i in sel else ''}{names[i]}", f"day_toggle_{i}") for i in range(4, 7)]
        return kb([r1, r2, [btn("✅ Готово", "days_done")], [btn("❌ Отмена", "cancel")]])

    async def _handle_calendar(self, cb, state, d):
        parts = d.split("_")
        if d.startswith("cal_prev") or d.startswith("cal_next"):
            y, m = int(parts[2]), int(parts[3])
            m = m - 1 if "prev" in d else m + 1
            if m < 1: m, y = 12, y - 1
            if m > 12: m, y = 1, y + 1
            await self.safe_edit(cb.message, None, self._calendar(y, m))
        elif d.startswith("cal_day"):
            y, m, day = int(parts[2]), int(parts[3]), int(parts[4])
            await state.update_data(scheduled_date=f"{day:02d}.{m:02d}.{y}", next_step="config")
            await self.safe_edit(cb.message, f"📅 <b>{day:02d}.{m:02d}.{y}</b>\n\n⏰ Время:", self._time_picker())

    async def _handle_time(self, cb, state, d):
        parts = d.split("_")
        t = f"{parts[1]}:{parts[2]}"
        data = await state.get_data()
        if data.get("multi_time"):
            sel = data.get("selected_times", [])
            if t in sel: sel.remove(t)
            else: sel.append(t)
            sel.sort()
            await state.update_data(selected_times=sel)
            await self.safe_edit(cb.message, f"⏰ <b>Выбрано:</b> {', '.join(sel) or 'нет'}", self._time_picker(True, sel))
        else:
            await state.update_data(scheduled_time=t)
            if data.get("next_step") == "days":
                await state.update_data(selected_days=[])
                await self.safe_edit(cb.message, f"⏰ {t}\n\n📅 <b>Дни:</b>", self._days_picker([]))
            else:
                await self._show_settings(cb.message, state)

    async def _handle_day(self, cb, state, day):
        data = await state.get_data()
        sel = data.get("selected_days", [])
        if day in sel: sel.remove(day)
        else: sel.append(day)
        await state.update_data(selected_days=sel)
        await self.safe_edit(cb.message, None, self._days_picker(sel))

    async def _show_settings(self, msg, state):
        data = await state.get_data()
        st, tm, dt = data.get("schedule_type", "once"), data.get("scheduled_time", ""), data.get("scheduled_date", "")
        info = ""
        if st == "once" and dt: info = f"📅 {dt} в {tm}"
        elif st == "daily": info = f"🔄 Ежедневно в {tm}"
        elif st == "weekly": info = f"📅 Еженедельно в {tm}"
        preview = (data.get("content", "")[:50] + "...") if len(data.get("content", "")) > 50 else (data.get("content") or "Медиа")
        text = f"⚙️ <b>Настройки</b>\n\n📝 {preview}\n{info}"
        await state.set_state(S.config)
        try: await self.safe_edit(msg, text, settings_kb(data))
        except: await self.bot.send_message(msg.chat.id, text, reply_markup=settings_kb(data), parse_mode=ParseMode.HTML)

    async def _send_preview(self, uid, state):
        data = await state.get_data()
        content, mt, fid = data.get("content", ""), data.get("content_type", "text"), data.get("media_file_id")
        spoiler, part = data.get("has_spoiler"), data.get("has_participate")
        markup = post_kb(0, part, data.get("button_text", "Участвовать"), data.get("url_buttons", []), 0)
        try:
            if mt == "text" or not fid: await self.bot.send_message(uid, content or "(пусто)", parse_mode=ParseMode.HTML, reply_markup=markup)
            elif mt == "photo": await self.bot.send_photo(uid, fid, caption=content, parse_mode=ParseMode.HTML, has_spoiler=spoiler, reply_markup=markup)
            elif mt == "video": await self.bot.send_video(uid, fid, caption=content, parse_mode=ParseMode.HTML, has_spoiler=spoiler, reply_markup=markup)
            else: await self.bot.send_document(uid, fid, caption=content, parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception as e: await self.bot.send_message(uid, f"❌ Ошибка: {e}")

    async def _send_post_preview(self, uid, post):
        content, mt, fid = post[3] or "", post[4], post[5]
        spoiler, part = post[15], post[16]
        markup = post_kb(post[0], part, post[17], json.loads(post[18]) if post[18] else [], await self.db.count_participants(post[0]))
        try:
            if mt == "text" or not fid: await self.bot.send_message(uid, content, parse_mode=ParseMode.HTML, reply_markup=markup)
            elif mt == "photo": await self.bot.send_photo(uid, fid, caption=content, parse_mode=ParseMode.HTML, has_spoiler=spoiler, reply_markup=markup)
            elif mt == "video": await self.bot.send_video(uid, fid, caption=content, parse_mode=ParseMode.HTML, has_spoiler=spoiler, reply_markup=markup)
        except: pass

    async def _save_post(self, msg, state, uid):
        data = await state.get_data()
        pid = await self.db.add_post(
            chat_id=data["chat_id"], owner_id=uid, content=data.get("content", ""),
            media_type=data.get("content_type"), media_file_id=data.get("media_file_id"),
            schedule_type=data.get("schedule_type", "once"), scheduled_time=data.get("scheduled_time", ""),
            scheduled_date=data.get("scheduled_date"), days_of_week=data.get("days_of_week"),
            pin_post=data.get("pin_post", 0), has_spoiler=data.get("has_spoiler", 0),
            has_participate=data.get("has_participate", 0), button_text=data.get("button_text", "Участвовать"),
            url_buttons=json.dumps(data.get("url_buttons", [])), template_name=data.get("template_name"))
        await self.db.update_stats(uid, created=1)
        await self._register_job(pid)
        await state.clear()
        await self.safe_edit(msg, f"✅ <b>Пост #{pid} сохранён!</b>", kb([[btn("📊 Посты", "posts")], [btn("📝 Новый", "new_post")], back_btn()]))

    async def _publish(self, msg, state, uid, with_settings=True):
        data = await state.get_data()
        pid = await self.db.add_post(
            chat_id=data["chat_id"], owner_id=uid, content=data.get("content", ""),
            media_type=data.get("content_type"), media_file_id=data.get("media_file_id"),
            schedule_type="instant", pin_post=data.get("pin_post", 0) if with_settings else 0,
            has_spoiler=data.get("has_spoiler", 0) if with_settings else 0,
            has_participate=data.get("has_participate", 0) if with_settings else 0,
            button_text=data.get("button_text", "Участвовать"),
            url_buttons=json.dumps(data.get("url_buttons", [])) if with_settings else "[]")
        sent = await self._execute(pid)
        await state.clear()
        status = "🚀 <b>Опубликовано!</b>" if sent else "❌ <b>Ошибка</b>"
        await self.safe_edit(msg, status, kb([[btn("📊 Посты", "posts")], [btn("📝 Новый", "new_post")], back_btn()]))

    async def _execute(self, pid):
        post = await self.db.get_post(pid)
        if not post: return None
        cid, uid = post[1], post[2]
        content, mt, fid = post[3] or "", post[4], post[5]
        pin, spoiler, part = post[14], post[15], post[16]
        btn_text = post[17]
        url_btns = json.loads(post[18]) if post[18] else []
        count = await self.db.count_participants(pid)
        markup = post_kb(pid, part, btn_text, url_btns, count)
        try:
            if mt == "text" or not fid: sent = await self.bot.send_message(cid, content, parse_mode=ParseMode.HTML, reply_markup=markup)
            elif mt == "photo": sent = await self.bot.send_photo(cid, fid, caption=content, parse_mode=ParseMode.HTML, has_spoiler=spoiler, reply_markup=markup)
            elif mt == "video": sent = await self.bot.send_video(cid, fid, caption=content, parse_mode=ParseMode.HTML, has_spoiler=spoiler, reply_markup=markup)
            else: sent = await self.bot.send_document(cid, fid, caption=content, parse_mode=ParseMode.HTML, reply_markup=markup)
            await self.db.update_post(pid, sent_message_id=sent.message_id, execution_count=post[13]+1, last_sent_at=datetime.now().isoformat())
            await self.db.update_stats(uid, sent=1)
            if pin:
                try: await self.bot.pin_chat_message(cid, sent.message_id, disable_notification=True)
                except: pass
            if post[6] == "once": await self.db.update_post(pid, is_active=0)
            return sent
        except Exception as e:
            logger.error(f"Execute {pid}: {e}")
            await self.db.update_stats(uid, failed=1)
            return None

    async def _register_job(self, pid):
        post = await self.db.get_post(pid)
        if not post or not post[10]: return
        st, tm, dt, dow = post[6], post[7], post[8], post[9]
        tz = pytz.timezone(await self.db.get_tz(post[2]))
        jid = f"post_{pid}"
        try: self.scheduler.remove_job(jid)
        except: pass
        if st == "once" and dt and tm:
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                d, mo, y = map(int, dt.split("."))
                run = tz.localize(datetime(y, mo, d, h, m))
                self.scheduler.add_job(self._execute, 'date', run_date=run, args=[pid], id=f"{jid}_{i}", replace_existing=True)
        elif st == "daily" and tm:
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                self.scheduler.add_job(self._execute, 'cron', hour=h, minute=m, timezone=tz, args=[pid], id=f"{jid}_{i}", replace_existing=True)
        elif st == "weekly" and tm and dow:
            days = ",".join(dow.split(","))
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                self.scheduler.add_job(self._execute, 'cron', day_of_week=days, hour=h, minute=m, timezone=tz, args=[pid], id=f"{jid}_{i}", replace_existing=True)

    async def _load_jobs(self):
        for (pid,) in await self.db.get_active_posts():
            await self._register_job(pid)
        logger.info("Jobs loaded")

    async def run(self):
        await self.db.init()
        self.scheduler.start()
        await self._load_jobs()
        # Start web server only if WEB_PORT is set
        port = os.getenv("WEB_PORT")
        if port:
            try:
                runner = web.AppRunner(self.web.app)
                await runner.setup()
                site = web.TCPSite(runner, '0.0.0.0', int(port))
                await site.start()
                logger.info(f"Web panel: http://localhost:{port}")
            except OSError as e:
                logger.warning(f"Web panel disabled: port {port} busy")
        logger.info("Bot started")
        await self.dp.start_polling(self.bot)


async def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not found")
        return
    await SchedulerBot(token).run()


if __name__ == "__main__":
    asyncio.run(main())
