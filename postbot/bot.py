"""Main bot module for PostBot"""
import os
import asyncio
import logging
from typing import Optional
from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

from .db import Database
from .web import WebPanel
from .handlers import (
    register_commands,
    register_post_handlers,
    register_template_handlers,
    register_callback_handlers
)

logger = logging.getLogger(__name__)


class SchedulerBot:
    """Main bot class with scheduler and web panel"""
    
    def __init__(self, token: str, db_path: str = "scheduler.db"):
        self.bot = Bot(token=token)
        self.db = Database(db_path)
        self.router = Router()
        self.scheduler = AsyncIOScheduler()
        self.web: Optional[WebPanel] = None
        
        # Try to use Redis for FSM storage if available
        self.storage = self._init_storage()
        self.dp = Dispatcher(storage=self.storage)
        self.dp.include_router(self.router)
        
        # Error notification callback
        async def notify_error(uid: int, pid: int, error: str):
            try:
                await self.bot.send_message(
                    uid,
                    f"⚠️ <b>Ошибка отправки</b>\n\n"
                    f"Пост #{pid}\n"
                    f"Ошибка: {error[:200]}",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        
        self._notify_error = notify_error
        self._register_handlers()

    def _init_storage(self):
        """Initialize FSM storage - Redis if available, else Memory"""
        redis_url = os.getenv("REDIS_URL")
        if redis_url:
            try:
                from aiogram.fsm.storage.redis import RedisStorage
                from redis.asyncio import Redis
                redis = Redis.from_url(redis_url)
                logger.info("Using Redis for FSM storage")
                return RedisStorage(redis)
            except ImportError:
                logger.warning("redis package not installed, falling back to MemoryStorage")
            except Exception as e:
                logger.warning(f"Redis connection failed: {e}, falling back to MemoryStorage")
        return MemoryStorage()

    def _register_handlers(self):
        """Register all handlers"""
        register_commands(self.router, self.db, self.bot)
        register_post_handlers(self.router, self.db, self.bot, self.scheduler, self._notify_error)
        register_template_handlers(self.router, self.db, self.bot)
        register_callback_handlers(self.router, self.db, self.bot)

    async def _load_jobs(self):
        """Load scheduled jobs from database"""
        active_posts = await self.db.get_active_posts()
        for (pid,) in active_posts:
            try:
                post = await self.db.get_post(pid)
                if post and post.is_active:
                    await self._register_single_job(pid)
            except Exception as e:
                logger.error(f"Failed to load job for post {pid}: {e}")
        logger.info(f"Loaded {len(active_posts)} scheduled jobs")

    async def _register_single_job(self, pid: int):
        """Register a single job for a post"""
        from datetime import datetime
        import pytz
        
        post = await self.db.get_post(pid)
        if not post or not post.is_active:
            return
        
        tz = pytz.timezone(await self.db.get_tz(post.owner_id))
        jid = f"post_{pid}"
        
        # Remove existing jobs
        for suffix in range(10):
            try:
                self.scheduler.remove_job(f"{jid}_{suffix}")
            except:
                pass
        
        async def execute():
            await self._execute_post(pid)
        
        st = post.schedule_type
        tm = post.scheduled_time
        
        if st == "once" and post.scheduled_date and tm:
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                d, mo, y = map(int, post.scheduled_date.split("."))
                run = tz.localize(datetime(y, mo, d, h, m))
                self.scheduler.add_job(execute, 'date', run_date=run, id=f"{jid}_{i}", replace_existing=True)
        elif st == "daily" and tm:
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                self.scheduler.add_job(execute, 'cron', hour=h, minute=m, timezone=tz, id=f"{jid}_{i}", replace_existing=True)
        elif st == "weekly" and tm and post.days_of_week:
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                self.scheduler.add_job(execute, 'cron', day_of_week=post.days_of_week, hour=h, minute=m,
                                       timezone=tz, id=f"{jid}_{i}", replace_existing=True)
        elif st == "monthly" and tm and post.day_of_month:
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                self.scheduler.add_job(execute, 'cron', day=post.day_of_month, hour=h, minute=m,
                                       timezone=tz, id=f"{jid}_{i}", replace_existing=True)

    async def _execute_post(self, pid: int) -> bool:
        """Execute a scheduled post"""
        from datetime import datetime
        from .keyboards import post_kb
        
        post = await self.db.get_post(pid)
        if not post:
            return False
        
        count = await self.db.count_participants(post.post_id)
        reaction_counts = await self.db.get_all_reaction_counts(post.post_id)
        markup = post_kb(post.post_id, post.has_participate_button, post.button_text, 
                        post.url_buttons, count, post.reaction_buttons, reaction_counts)
        
        try:
            if post.media_type == "text" or not post.media_file_id:
                sent = await self.bot.send_message(post.chat_id, post.content, parse_mode=ParseMode.HTML, reply_markup=markup)
            elif post.media_type == "photo":
                sent = await self.bot.send_photo(post.chat_id, post.media_file_id, caption=post.content,
                                                 parse_mode=ParseMode.HTML, has_spoiler=post.has_spoiler, reply_markup=markup)
            elif post.media_type == "video":
                sent = await self.bot.send_video(post.chat_id, post.media_file_id, caption=post.content,
                                                 parse_mode=ParseMode.HTML, has_spoiler=post.has_spoiler, reply_markup=markup)
            else:
                sent = await self.bot.send_document(post.chat_id, post.media_file_id, caption=post.content,
                                                    parse_mode=ParseMode.HTML, reply_markup=markup)
            
            await self.db.update_post(pid, sent_message_id=sent.message_id,
                                      execution_count=post.execution_count + 1,
                                      last_sent_at=datetime.now().isoformat())
            await self.db.update_stats(post.owner_id, sent=1)
            await self.db.add_history(pid, post.chat_id, sent.message_id, True)
            
            if post.pin_post:
                try:
                    await self.bot.pin_chat_message(post.chat_id, sent.message_id, disable_notification=True)
                except:
                    pass
            
            if post.schedule_type == "once":
                await self.db.update_post(pid, is_active=0)
            
            return True
        except Exception as e:
            logger.error(f"Execute post {pid}: {e}")
            await self.db.update_stats(post.owner_id, failed=1)
            await self.db.add_history(pid, post.chat_id, 0, False, str(e))
            await self._notify_error(post.owner_id, pid, str(e))
            return False

    async def run(self):
        """Start the bot"""
        await self.db.init()
        self.scheduler.start()
        await self._load_jobs()
        
        # Start web server if port is set
        port = os.getenv("WEB_PORT")
        if port:
            try:
                self.web = WebPanel(self.db, self.bot)
                runner = web.AppRunner(self.web.app)
                await runner.setup()
                site = web.TCPSite(runner, '0.0.0.0', int(port))
                await site.start()
                logger.info(f"Web panel started on http://localhost:{port}")
            except OSError as e:
                logger.warning(f"Web panel disabled: {e}")
        
        logger.info("Bot started")
        try:
            await self.dp.start_polling(self.bot)
        finally:
            await self.db.close()
            self.scheduler.shutdown()
