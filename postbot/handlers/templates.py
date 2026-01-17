"""Template handlers for PostBot"""
import json
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode, ChatType
from aiogram.exceptions import TelegramBadRequest

from ..db import Database
from ..states import S
from ..keyboards import kb, btn, back_btn, main_kb, templates_kb

logger = logging.getLogger(__name__)


def register_template_handlers(router: Router, db: Database, bot: Bot):
    """Register template-related handlers"""

    async def safe_edit(msg, text=None, markup=None):
        try:
            if text:
                return await msg.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            return await msg.edit_reply_markup(reply_markup=markup)
        except TelegramBadRequest:
            pass

    @router.callback_query(F.data == "templates")
    async def cb_templates(cb: CallbackQuery):
        templates = await db.get_templates(cb.from_user.id)
        await safe_edit(cb.message, "📑 <b>Шаблоны:</b>", templates_kb(templates))

    @router.callback_query(F.data == "new_template")
    async def cb_new_template(cb: CallbackQuery, state: FSMContext):
        await safe_edit(cb.message, "📑 <b>Введите название шаблона:</b>")
        await state.set_state(S.template_name)

    @router.message(S.template_name, F.chat.type == ChatType.PRIVATE)
    async def on_template_name(msg: Message, state: FSMContext):
        name = msg.text.strip()
        data = await state.get_data()
        
        if data.get("content") or data.get("media_file_id"):
            # Saving current post as template
            await db.add_template(
                msg.from_user.id, name, data.get("content"),
                data.get("media_type"), data.get("media_file_id"),
                int(data.get("pin_post", 0)), int(data.get("has_spoiler", 0)),
                int(data.get("has_participate", 0)), data.get("button_text", "Участвовать"),
                json.dumps(data.get("url_buttons", []))
            )
            await msg.answer(f"💾 Шаблон «{name}» сохранён!", reply_markup=main_kb(), parse_mode=ParseMode.HTML)
            await state.clear()
        else:
            # Creating new template - ask for content
            await state.update_data(template_name=name)
            await msg.answer("📝 <b>Введите текст шаблона:</b>", parse_mode=ParseMode.HTML)
            await state.set_state(S.template_content)

    @router.message(S.template_content, F.chat.type == ChatType.PRIVATE)
    async def on_template_content(msg: Message, state: FSMContext):
        data = await state.get_data()
        name = data.get("template_name", "Без имени")
        content = msg.text or ""
        await db.add_template(msg.from_user.id, name, content)
        await msg.answer(f"💾 Шаблон «{name}» сохранён!", reply_markup=main_kb(), parse_mode=ParseMode.HTML)
        await state.clear()

    @router.callback_query(F.data.startswith("tpl_") & ~F.data.startswith("tpl_use") & ~F.data.startswith("tpl_del"))
    async def cb_template_detail(cb: CallbackQuery):
        tid = int(cb.data.split("_")[1])
        tpl = await db.get_template(tid)
        if not tpl:
            return await cb.answer("Не найден", show_alert=True)
        
        text = f"📑 <b>{tpl.name}</b>\n\n{(tpl.content or 'Медиа')[:200]}"
        await safe_edit(cb.message, text, kb([
            [btn("📝 Использовать", f"use_tpl_{tid}")],
            [btn("🗑 Удалить", f"del_tpl_{tid}")],
            back_btn("templates")
        ]))

    @router.callback_query(F.data.startswith("use_tpl_"))
    async def cb_use_template(cb: CallbackQuery, state: FSMContext):
        tid = int(cb.data.split("_")[2])
        tpl = await db.get_template(tid)
        if not tpl:
            return await cb.answer("Не найден", show_alert=True)
        
        chats = await db.get_chats(cb.from_user.id)
        if not chats:
            return await cb.answer("Нет чатов", show_alert=True)
        
        # Load template data into state
        await state.update_data(
            content=tpl.content,
            media_type=tpl.media_type,
            media_file_id=tpl.media_file_id,
            pin_post=tpl.pin_post,
            has_spoiler=tpl.has_spoiler,
            has_participate=tpl.has_participate_button,
            button_text=tpl.button_text,
            url_buttons=[{"text": b.text, "url": b.url} for b in tpl.url_buttons],
            template_name=tpl.name,
            content_type=tpl.media_type or "text",
            selected_chats=[chats[0].chat_id] if len(chats) == 1 else []
        )
        
        if len(chats) == 1:
            # Single chat - show schedule options
            from ..keyboards import schedule_kb
            await safe_edit(cb.message, f"📝 Шаблон «{tpl.name}»\n\n<b>Когда опубликовать?</b>", schedule_kb())
        else:
            # Multiple chats - select first
            from ..keyboards import chats_select_kb
            rows = [[btn(f"{'📢' if c.chat_type == 'channel' else '👥'} {c.chat_title}", f"chat_{c.chat_id}")] 
                    for c in chats] + [back_btn()]
            await safe_edit(cb.message, f"📝 Шаблон «{tpl.name}»\n\n<b>Выберите чат:</b>", kb(rows))

    @router.callback_query(F.data.startswith("del_tpl_"))
    async def cb_delete_template(cb: CallbackQuery):
        tid = int(cb.data.split("_")[2])
        await db.delete_template(tid)
        await cb.answer("🗑 Удалён", show_alert=True)
        templates = await db.get_templates(cb.from_user.id)
        await safe_edit(cb.message, "📑 <b>Шаблоны:</b>", templates_kb(templates))

    @router.callback_query(F.data == "save_template")
    async def cb_save_as_template(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        if not data.get("content") and not data.get("media_file_id"):
            return await cb.answer("Нет контента для сохранения", show_alert=True)
        await safe_edit(cb.message, "💾 <b>Название шаблона:</b>")
        await state.set_state(S.template_name)

    @router.callback_query(F.data == "from_template")
    async def cb_from_template(cb: CallbackQuery, state: FSMContext):
        templates = await db.get_templates(cb.from_user.id)
        if not templates:
            return await cb.answer("Нет шаблонов", show_alert=True)
        
        rows = [[btn(f"📑 {t.name}", f"apply_tpl_{t.template_id}")] for t in templates]
        rows.append(back_btn("back_settings"))
        await safe_edit(cb.message, "📑 <b>Выберите шаблон:</b>", kb(rows))

    @router.callback_query(F.data.startswith("apply_tpl_"))
    async def cb_apply_template(cb: CallbackQuery, state: FSMContext):
        tid = int(cb.data.split("_")[2])
        tpl = await db.get_template(tid)
        if not tpl:
            return await cb.answer("Не найден", show_alert=True)
        
        data = await state.get_data()
        await state.update_data(
            content=tpl.content,
            media_type=tpl.media_type,
            media_file_id=tpl.media_file_id,
            content_type=tpl.media_type or "text",
            pin_post=tpl.pin_post,
            has_spoiler=tpl.has_spoiler,
            has_participate=tpl.has_participate_button,
            button_text=tpl.button_text,
            url_buttons=[{"text": b.text, "url": b.url} for b in tpl.url_buttons]
        )
        await cb.answer(f"✅ Шаблон «{tpl.name}» применён")
        
        # Show settings
        from ..keyboards import settings_kb
        st = data.get("schedule_type", "once")
        tm = data.get("scheduled_time", "")
        dt = data.get("scheduled_date", "")
        
        info = ""
        if st == "once" and dt:
            info = f"📅 {dt} в {tm}"
        elif st == "daily":
            info = f"🔄 Ежедневно в {tm}"
        elif st == "weekly":
            info = f"📅 Еженедельно в {tm}"
        
        preview = (tpl.content[:50] + "...") if len(tpl.content or "") > 50 else (tpl.content or "Медиа")
        text = f"⚙️ <b>Настройки</b>\n\n📝 {preview}\n{info}"
        new_data = await state.get_data()
        await safe_edit(cb.message, text, settings_kb(new_data))
