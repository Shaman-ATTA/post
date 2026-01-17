"""General callback handlers for PostBot"""
import os
import json
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode, ChatType
from aiogram.exceptions import TelegramBadRequest

from ..db import Database
from ..states import S
from ..keyboards import kb, btn, back_btn, main_kb, tz_kb

logger = logging.getLogger(__name__)


def register_callback_handlers(router: Router, db: Database, bot: Bot):
    """Register general callback handlers"""

    async def safe_edit(msg, text=None, markup=None):
        try:
            if text:
                return await msg.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            return await msg.edit_reply_markup(reply_markup=markup)
        except TelegramBadRequest:
            pass

    @router.callback_query(F.data == "main")
    async def cb_main(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await safe_edit(cb.message, "👋 <b>Главное меню</b>", main_kb())

    @router.callback_query(F.data == "chats")
    async def cb_chats(cb: CallbackQuery):
        chats = await db.get_chats(cb.from_user.id)
        if not chats:
            return await cb.answer("Нет чатов. Добавьте бота в канал/группу как админа.", show_alert=True)
        
        rows = [[btn(f"{'📢' if c.chat_type == 'channel' else '👥'} {c.chat_title}", f"info_{c.chat_id}")] 
                for c in chats]
        rows.append(back_btn())
        await safe_edit(cb.message, "📋 <b>Ваши чаты:</b>", kb(rows))

    @router.callback_query(F.data.startswith("info_"))
    async def cb_chat_info(cb: CallbackQuery):
        cid = int(cb.data.split("_")[1])
        chat = await db.get_chat(cid)
        if not chat:
            return await cb.answer("Чат не найден", show_alert=True)
        
        icon = "📢" if chat.chat_type == "channel" else "👥"
        text = (f"{icon} <b>{chat.chat_title}</b>\n\n"
                f"ID: <code>{chat.chat_id}</code>\n"
                f"Тип: {chat.chat_type}\n"
                f"Добавлен: {chat.added_date[:10] if chat.added_date else '-'}")
        await safe_edit(cb.message, text, kb([back_btn("chats")]))

    @router.callback_query(F.data == "plan")
    async def cb_plan(cb: CallbackQuery):
        posts = await db.get_posts(cb.from_user.id, filter_type="active", limit=20)
        scheduled = [p for p in posts if p.schedule_type != "instant"]
        
        if not scheduled:
            return await cb.answer("Нет запланированных постов", show_alert=True)
        
        text = "📅 <b>Контент-план</b>\n\n"
        for p in scheduled:
            icon = "📌" if p.schedule_type == "once" else "🔄"
            time_info = p.scheduled_time or ""
            date_info = p.scheduled_date or ""
            if p.schedule_type == "monthly" and p.day_of_month:
                date_info = f"{p.day_of_month}-го"
            text += f"{icon} <b>{date_info} {time_info}</b>\n"
            text += f"└ #{p.post_id}: {(p.content or 'Медиа')[:30]}\n\n"
        
        await safe_edit(cb.message, text, kb([back_btn()]))

    @router.callback_query(F.data == "export_import")
    async def cb_export_import(cb: CallbackQuery):
        await safe_edit(cb.message, "📤📥 <b>Экспорт / Импорт</b>\n\nВыберите действие:", kb([
            [btn("📤 Экспорт в JSON", "export")],
            [btn("📥 Импорт из JSON", "import")],
            back_btn()
        ]))

    @router.callback_query(F.data == "export")
    async def cb_export(cb: CallbackQuery):
        data = await db.export_posts(cb.from_user.id)
        if not data:
            return await cb.answer("Нет постов для экспорта", show_alert=True)
        
        file = BufferedInputFile(
            json.dumps(data, ensure_ascii=False, indent=2).encode(),
            filename="posts_export.json"
        )
        await bot.send_document(cb.from_user.id, file, caption="📤 Экспорт постов")
        await cb.answer()

    @router.callback_query(F.data == "import")
    async def cb_import(cb: CallbackQuery, state: FSMContext):
        await safe_edit(cb.message, "📥 <b>Отправьте JSON файл с постами:</b>\n\n"
                                    "Формат: массив объектов с полями content, schedule_type, scheduled_time и т.д.")
        await state.set_state(S.import_file)

    @router.message(S.import_file)
    async def on_import_file(msg: Message, state: FSMContext):
        if not msg.document:
            return await msg.answer("❌ Отправьте JSON файл")
        
        file = await bot.get_file(msg.document.file_id)
        data = await bot.download_file(file.file_path)
        
        try:
            posts = json.loads(data.read().decode())
        except:
            return await msg.answer("❌ Неверный формат JSON")
        
        chats = await db.get_chats(msg.from_user.id)
        if not chats:
            return await msg.answer("❌ Сначала добавьте бота в чат")
        
        cid = chats[0].chat_id
        count = 0
        
        for p in posts:
            await db.add_post(
                chat_id=cid, owner_id=msg.from_user.id, content=p.get('content', ''),
                media_type=p.get('media_type'), schedule_type=p.get('schedule_type', 'instant'),
                scheduled_time=p.get('scheduled_time', ''), scheduled_date=p.get('scheduled_date'),
                days_of_week=p.get('days_of_week'), day_of_month=p.get('day_of_month'),
                pin_post=p.get('pin_post', 0), has_spoiler=p.get('has_spoiler', 0),
                has_participate=p.get('has_participate', 0), button_text=p.get('button_text', 'Участвовать'),
                url_buttons=json.dumps(p.get('url_buttons', []))
            )
            count += 1
        
        await msg.answer(f"✅ Импортировано: {count} постов", reply_markup=main_kb())
        await state.clear()

    @router.callback_query(F.data == "web_panel")
    async def cb_web_panel(cb: CallbackQuery):
        token = await db.get_user_token(cb.from_user.id)
        port = os.getenv("WEB_PORT", "8080")
        host = os.getenv("WEB_HOST", "localhost")
        url = f"http://{host}:{port}/?token={token}"
        
        await safe_edit(cb.message, 
            f"🌐 <b>Веб-панель</b>\n\n"
            f"<a href='{url}'>Открыть панель</a>\n\n"
            f"⚠️ Не делитесь этой ссылкой — она содержит ваш токен доступа!",
            kb([back_btn()]))

    @router.callback_query(F.data == "settings")
    async def cb_settings(cb: CallbackQuery):
        tz = await db.get_tz(cb.from_user.id)
        stats = await db.get_stats(cb.from_user.id)
        
        text = (f"⚙️ <b>Настройки</b>\n\n"
                f"🌍 Часовой пояс: {tz}\n\n"
                f"📊 <b>Статистика:</b>\n"
                f"📝 Создано: {stats.posts_created if stats else 0}\n"
                f"✅ Отправлено: {stats.posts_sent if stats else 0}\n"
                f"❌ Ошибок: {stats.posts_failed if stats else 0}")
        
        await safe_edit(cb.message, text, kb([
            [btn(f"🌍 Изменить часовой пояс", "change_tz")],
            back_btn()
        ]))

    @router.callback_query(F.data == "change_tz")
    async def cb_change_tz(cb: CallbackQuery):
        await safe_edit(cb.message, "🌍 <b>Выберите часовой пояс:</b>", tz_kb())

    @router.callback_query(F.data.startswith("tz_"))
    async def cb_set_tz(cb: CallbackQuery):
        tz = cb.data[3:]
        await db.set_tz(cb.from_user.id, tz)
        await cb.answer(f"✅ Часовой пояс: {tz}", show_alert=True)
        # Return to settings
        stats = await db.get_stats(cb.from_user.id)
        text = (f"⚙️ <b>Настройки</b>\n\n"
                f"🌍 Часовой пояс: {tz}\n\n"
                f"📊 <b>Статистика:</b>\n"
                f"📝 Создано: {stats.posts_created if stats else 0}\n"
                f"✅ Отправлено: {stats.posts_sent if stats else 0}\n"
                f"❌ Ошибок: {stats.posts_failed if stats else 0}")
        await safe_edit(cb.message, text, kb([
            [btn(f"🌍 Изменить часовой пояс", "change_tz")],
            back_btn()
        ]))

    @router.callback_query(F.data == "x")
    async def cb_noop(cb: CallbackQuery):
        await cb.answer()

    @router.callback_query(F.data.startswith("chat_"))
    async def cb_select_single_chat(cb: CallbackQuery, state: FSMContext):
        """Handle chat selection for template application"""
        cid = int(cb.data.split("_")[1])
        data = await state.get_data()
        await state.update_data(selected_chats=[cid])
        
        # If coming from template, show schedule
        if data.get("template_name"):
            from ..keyboards import schedule_kb
            await safe_edit(cb.message, "⏱ <b>Когда опубликовать?</b>", schedule_kb())
