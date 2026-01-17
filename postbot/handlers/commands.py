"""Command handlers for PostBot"""
import logging
from aiogram import Router, F
from aiogram.types import Message, ChatMemberUpdated
from aiogram.filters import Command
from aiogram.enums import ParseMode, ChatType

from ..db import Database
from ..keyboards import main_kb

logger = logging.getLogger(__name__)


def register_commands(router: Router, db: Database, bot):
    """Register command handlers"""
    
    @router.message(Command("start"), F.chat.type == ChatType.PRIVATE)
    async def cmd_start(msg: Message):
        await db.add_user(msg.from_user.id, msg.from_user.username)
        await msg.answer(
            "👋 <b>PostBot</b> — отложенный постинг\n\n"
            "🤖 Добавьте меня в группу/канал как админа!\n\n"
            "📊 Возможности:\n"
            "• Отложенные публикации\n"
            "• Шаблоны постов\n"
            "• Веб-панель управления\n"
            "• Экспорт/импорт в JSON\n"
            "• Кнопки URL и «Участвовать»",
            reply_markup=main_kb(),
            parse_mode=ParseMode.HTML
        )

    @router.message(Command("help"), F.chat.type == ChatType.PRIVATE)
    async def cmd_help(msg: Message):
        await msg.answer(
            "<b>📖 Команды:</b>\n\n"
            "/start — Главное меню\n"
            "/help — Справка\n"
            "/stats — Статистика\n\n"
            "<b>🔧 Как использовать:</b>\n"
            "1. Добавьте бота в канал/группу как админа\n"
            "2. Создайте пост через меню\n"
            "3. Выберите время публикации\n"
            "4. Готово!",
            parse_mode=ParseMode.HTML
        )

    @router.message(Command("stats"), F.chat.type == ChatType.PRIVATE)
    async def cmd_stats(msg: Message):
        stats = await db.get_stats(msg.from_user.id)
        if not stats:
            return await msg.answer("📊 Статистика пока пуста")
        await msg.answer(
            f"📊 <b>Ваша статистика</b>\n\n"
            f"📝 Создано постов: {stats.posts_created}\n"
            f"✅ Отправлено: {stats.posts_sent}\n"
            f"❌ Ошибок: {stats.posts_failed}",
            parse_mode=ParseMode.HTML
        )

    @router.my_chat_member()
    async def on_added(ev: ChatMemberUpdated):
        if ev.new_chat_member.status == "administrator":
            await db.add_chat(
                ev.chat.id,
                ev.chat.title or "Без названия",
                ev.chat.type,
                ev.from_user.id
            )
            try:
                await bot.send_message(
                    ev.from_user.id,
                    f"✅ Бот добавлен в <b>{ev.chat.title}</b>!\n\n"
                    "Теперь вы можете создавать посты для этого чата.",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        elif ev.new_chat_member.status in ("left", "kicked"):
            logger.info(f"Bot removed from chat {ev.chat.id}")
