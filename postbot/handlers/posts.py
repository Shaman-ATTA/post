"""Post creation and editing handlers"""
import json
import logging
from datetime import datetime
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode, ChatType
from aiogram.exceptions import TelegramBadRequest
import pytz

from ..db import Database
from ..models import Post, UrlButton
from ..states import S
from ..keyboards import (
    kb, btn, back_btn, main_kb, schedule_kb, settings_kb, post_kb,
    post_manage_kb, post_edit_kb, posts_filter_kb, pagination_kb,
    calendar_kb, time_picker_kb, days_picker_kb, monthly_day_picker_kb,
    chats_select_kb, confirm_kb, reaction_buttons_kb, reaction_presets_kb
)

logger = logging.getLogger(__name__)
POSTS_PER_PAGE = 10


def register_post_handlers(router: Router, db: Database, bot: Bot, scheduler, notify_error):
    """Register post-related handlers"""

    async def safe_edit(msg, text=None, markup=None):
        try:
            if text:
                return await msg.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            return await msg.edit_reply_markup(reply_markup=markup)
        except TelegramBadRequest:
            pass

    # ==================== Post List & Filtering ====================
    
    @router.callback_query(F.data == "posts")
    async def cb_posts(cb: CallbackQuery, state: FSMContext):
        await state.update_data(posts_filter="all", posts_page=0)
        await _show_posts_list(cb, state, db)

    @router.callback_query(F.data.startswith("filter_"))
    async def cb_filter(cb: CallbackQuery, state: FSMContext):
        filter_type = cb.data.split("_")[1]
        await state.update_data(posts_filter=filter_type, posts_page=0)
        await _show_posts_list(cb, state, db)

    @router.callback_query(F.data.startswith("posts_page_"))
    async def cb_posts_page(cb: CallbackQuery, state: FSMContext):
        page = int(cb.data.split("_")[2])
        await state.update_data(posts_page=page)
        await _show_posts_list(cb, state, db)

    async def _show_posts_list(cb: CallbackQuery, state: FSMContext, db: Database):
        uid = cb.from_user.id
        data = await state.get_data()
        filter_type = data.get("posts_filter", "all")
        page = data.get("posts_page", 0)
        
        total = await db.count_posts(uid, filter_type)
        if total == 0:
            return await cb.answer("Нет постов", show_alert=True)
        
        total_pages = (total + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
        posts = await db.get_posts(uid, filter_type, POSTS_PER_PAGE, page * POSTS_PER_PAGE)
        
        rows = [[btn(f"{'✅' if p.is_active else '❌'} #{p.post_id}: {(p.content or 'Медиа')[:20]}",
                     f"post_{p.post_id}")] for p in posts]
        
        if total_pages > 1:
            rows.append(pagination_kb(page, total_pages, "posts"))
        
        filter_names = {"all": "Все", "active": "Активные", "inactive": "Неактивные"}
        rows.append([btn(f"🔄 {filter_names[filter_type]}", f"toggle_filter_{filter_type}")])
        rows.append([btn("🗑 Удалить все", "bulk_delete"), btn("❌ Откл все", "bulk_disable")])
        rows.append(back_btn())
        
        await safe_edit(cb.message, f"📊 <b>Посты</b> ({total})", kb(rows))

    @router.callback_query(F.data.startswith("toggle_filter_"))
    async def cb_toggle_filter(cb: CallbackQuery, state: FSMContext):
        current = cb.data.split("_")[2]
        filters = ["all", "active", "inactive"]
        next_idx = (filters.index(current) + 1) % len(filters)
        await state.update_data(posts_filter=filters[next_idx], posts_page=0)
        await _show_posts_list(cb, state, db)

    # ==================== Bulk Operations ====================

    @router.callback_query(F.data == "bulk_delete")
    async def cb_bulk_delete(cb: CallbackQuery):
        await safe_edit(cb.message, 
            "⚠️ <b>Удалить ВСЕ посты?</b>\n\nЭто действие необратимо!", 
            confirm_kb("bulk_delete"))

    @router.callback_query(F.data == "confirm_bulk_delete")
    async def cb_confirm_bulk_delete(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        filter_type = data.get("posts_filter", "all")
        await db.delete_posts_bulk(cb.from_user.id, filter_type)
        # Remove all jobs
        for job in scheduler.get_jobs():
            if job.id.startswith("post_"):
                try: scheduler.remove_job(job.id)
                except: pass
        await cb.answer("🗑 Все посты удалены", show_alert=True)
        await safe_edit(cb.message, "👋 <b>Главное меню</b>", main_kb())

    @router.callback_query(F.data == "bulk_disable")
    async def cb_bulk_disable(cb: CallbackQuery):
        await safe_edit(cb.message, 
            "⚠️ <b>Отключить ВСЕ посты?</b>", 
            confirm_kb("bulk_disable"))

    @router.callback_query(F.data == "confirm_bulk_disable")
    async def cb_confirm_bulk_disable(cb: CallbackQuery):
        await db.disable_posts_bulk(cb.from_user.id)
        for job in scheduler.get_jobs():
            if job.id.startswith("post_"):
                try: scheduler.remove_job(job.id)
                except: pass
        await cb.answer("❌ Все посты отключены", show_alert=True)
        await safe_edit(cb.message, "👋 <b>Главное меню</b>", main_kb())

    # ==================== Post Details ====================

    @router.callback_query(F.data.startswith("post_"))
    async def cb_post_detail(cb: CallbackQuery):
        pid = int(cb.data.split("_")[1])
        post = await db.get_post(pid)
        if not post:
            return await cb.answer("Не найден", show_alert=True)
        
        schedule_info = _format_schedule(post)
        info = (f"📋 <b>Пост #{pid}</b>\n\n"
                f"{'✅ Активен' if post.is_active else '❌ Отключен'}\n"
                f"{schedule_info}\n\n"
                f"📝 {(post.content or 'Медиа')[:200]}")
        
        rows = [
            [btn("👁 Превью", f"view_{pid}")],
            [btn("✏️ Редактировать", f"edit_{pid}")],
            [btn("📋 Дублировать", f"dup_{pid}")],
            [btn("❌ Откл" if post.is_active else "✅ Вкл", f"toggle_{pid}")],
        ]
        if post.has_participate_button:
            count = await db.count_participants(pid)
            rows.append([btn(f"👥 Участники ({count})", f"participants_{pid}")])
        rows.append([btn("🗑 Удалить", f"del_{pid}")])
        rows.append(back_btn("posts"))
        
        await safe_edit(cb.message, info, kb(rows))

    def _format_schedule(post: Post) -> str:
        if post.schedule_type == "instant":
            return "🚀 Мгновенная публикация"
        elif post.schedule_type == "once":
            return f"📅 Один раз: {post.scheduled_date} в {post.scheduled_time}"
        elif post.schedule_type == "daily":
            return f"🔄 Ежедневно в {post.scheduled_time}"
        elif post.schedule_type == "weekly":
            days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            day_names = [days[int(d)] for d in (post.days_of_week or "").split(",") if d]
            return f"📅 {', '.join(day_names)} в {post.scheduled_time}"
        elif post.schedule_type == "monthly":
            return f"🗓 {post.day_of_month}-го числа в {post.scheduled_time}"
        return ""

    @router.callback_query(F.data.startswith("view_"))
    async def cb_view_post(cb: CallbackQuery):
        pid = int(cb.data.split("_")[1])
        post = await db.get_post(pid)
        if not post:
            return await cb.answer("Не найден", show_alert=True)
        await _send_post_preview(cb.from_user.id, post, db, bot)
        await cb.answer()

    @router.callback_query(F.data.startswith("dup_"))
    async def cb_duplicate_post(cb: CallbackQuery):
        pid = int(cb.data.split("_")[1])
        new_pid = await db.duplicate_post(pid)
        if new_pid:
            await cb.answer(f"📋 Создана копия #{new_pid}", show_alert=True)
        else:
            await cb.answer("❌ Ошибка", show_alert=True)

    @router.callback_query(F.data.startswith("toggle_") & ~F.data.contains("filter") & ~F.data.contains("pin") 
                          & ~F.data.contains("spoiler") & ~F.data.contains("participate"))
    async def cb_toggle_post(cb: CallbackQuery):
        pid = int(cb.data.split("_")[1])
        post = await db.get_post(pid)
        if not post:
            return await cb.answer("Не найден", show_alert=True)
        new_active = not post.is_active
        await db.update_post(pid, is_active=int(new_active))
        if new_active:
            await _register_job(pid, db, scheduler, bot, notify_error)
        else:
            _remove_job(pid, scheduler)
        await cb.answer("✅ Включен" if new_active else "❌ Отключен")
        # Refresh view
        post = await db.get_post(pid)
        schedule_info = _format_schedule(post)
        info = (f"📋 <b>Пост #{pid}</b>\n\n"
                f"{'✅ Активен' if post.is_active else '❌ Отключен'}\n"
                f"{schedule_info}\n\n"
                f"📝 {(post.content or 'Медиа')[:200]}")
        rows = [
            [btn("👁 Превью", f"view_{pid}")],
            [btn("✏️ Редактировать", f"edit_{pid}")],
            [btn("📋 Дублировать", f"dup_{pid}")],
            [btn("❌ Откл" if post.is_active else "✅ Вкл", f"toggle_{pid}")],
        ]
        if post.has_participate_button:
            count = await db.count_participants(pid)
            rows.append([btn(f"👥 Участники ({count})", f"participants_{pid}")])
        rows.append([btn("🗑 Удалить", f"del_{pid}")])
        rows.append(back_btn("posts"))
        await safe_edit(cb.message, info, kb(rows))

    @router.callback_query(F.data.startswith("del_") & ~F.data.startswith("del_tpl"))
    async def cb_delete_post(cb: CallbackQuery, state: FSMContext):
        pid = int(cb.data.split("_")[1])
        await db.delete_post(pid)
        _remove_job(pid, scheduler)
        await cb.answer("🗑 Удалён", show_alert=True)
        await state.update_data(posts_page=0)
        # Check if there are more posts
        total = await db.count_posts(cb.from_user.id)
        if total == 0:
            await safe_edit(cb.message, "👋 <b>Главное меню</b>", main_kb())
        else:
            await _show_posts_list(cb, state, db)

    @router.callback_query(F.data.startswith("participants_"))
    async def cb_participants(cb: CallbackQuery):
        pid = int(cb.data.split("_")[1])
        participants = await db.get_participants(pid, limit=20)
        if not participants:
            return await cb.answer("Нет участников", show_alert=True)
        text = f"👥 <b>Участники поста #{pid}</b>\n\n"
        for i, p in enumerate(participants, 1):
            text += f"{i}. @{p.username} <i>({p.joined_at[:10]})</i>\n"
        count = await db.count_participants(pid)
        if count > 20:
            text += f"\n<i>...и ещё {count - 20}</i>"
        await safe_edit(cb.message, text, kb([back_btn(f"post_{pid}")]))

    # ==================== Post Editing ====================

    @router.callback_query(F.data.startswith("edit_") & ~F.data.contains("content") 
                          & ~F.data.contains("media") & ~F.data.contains("time")
                          & ~F.data.contains("urls") & ~F.data.contains("settings"))
    async def cb_edit_post(cb: CallbackQuery):
        pid = int(cb.data.split("_")[1])
        post = await db.get_post(pid)
        if not post:
            return await cb.answer("Не найден", show_alert=True)
        await safe_edit(cb.message, f"✏️ <b>Редактировать пост #{pid}</b>", post_edit_kb(pid))

    @router.callback_query(F.data.startswith("edit_content_"))
    async def cb_edit_content(cb: CallbackQuery, state: FSMContext):
        pid = int(cb.data.split("_")[2])
        await state.update_data(editing_post_id=pid)
        await safe_edit(cb.message, "✍️ <b>Введите новый текст:</b>")
        await state.set_state(S.edit_content)

    @router.message(S.edit_content, F.chat.type == ChatType.PRIVATE)
    async def on_edit_content(msg: Message, state: FSMContext):
        data = await state.get_data()
        pid = data.get("editing_post_id")
        if pid:
            await db.update_post(pid, content=msg.text)
            await msg.answer(f"✅ Текст поста #{pid} обновлён", reply_markup=main_kb(), parse_mode=ParseMode.HTML)
        await state.clear()

    @router.callback_query(F.data.startswith("edit_media_"))
    async def cb_edit_media(cb: CallbackQuery, state: FSMContext):
        pid = int(cb.data.split("_")[2])
        await state.update_data(editing_post_id=pid)
        await safe_edit(cb.message, "🖼 <b>Отправьте новое фото/видео:</b>")
        await state.set_state(S.edit_media)

    @router.message(S.edit_media, F.chat.type == ChatType.PRIVATE)
    async def on_edit_media(msg: Message, state: FSMContext):
        data = await state.get_data()
        pid = data.get("editing_post_id")
        fid, mt = None, None
        if msg.photo:
            fid, mt = msg.photo[-1].file_id, "photo"
        elif msg.video:
            fid, mt = msg.video.file_id, "video"
        elif msg.document:
            fid, mt = msg.document.file_id, "document"
        if pid and fid:
            await db.update_post(pid, media_file_id=fid, media_type=mt)
            await msg.answer(f"✅ Медиа поста #{pid} обновлено", reply_markup=main_kb(), parse_mode=ParseMode.HTML)
        else:
            await msg.answer("❌ Отправьте медиа файл")
            return
        await state.clear()

    @router.callback_query(F.data.startswith("edit_time_"))
    async def cb_edit_time(cb: CallbackQuery, state: FSMContext):
        pid = int(cb.data.split("_")[2])
        await state.update_data(editing_post_id=pid)
        await safe_edit(cb.message, "⏰ <b>Введите новое время (HH:MM):</b>")
        await state.set_state(S.edit_time)

    @router.message(S.edit_time, F.chat.type == ChatType.PRIVATE)
    async def on_edit_time(msg: Message, state: FSMContext):
        data = await state.get_data()
        pid = data.get("editing_post_id")
        try:
            h, m = map(int, msg.text.strip().split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
            time_str = f"{h:02d}:{m:02d}"
            if pid:
                await db.update_post(pid, scheduled_time=time_str)
                await _register_job(pid, db, scheduler, bot, notify_error)
                await msg.answer(f"✅ Время поста #{pid} обновлено: {time_str}", reply_markup=main_kb())
        except:
            await msg.answer("❌ Формат: HH:MM")
            return
        await state.clear()

    @router.callback_query(F.data.startswith("edit_settings_"))
    async def cb_edit_settings(cb: CallbackQuery):
        pid = int(cb.data.split("_")[2])
        post = await db.get_post(pid)
        if not post:
            return await cb.answer("Не найден", show_alert=True)
        rows = [
            [btn(f"{'✅' if post.pin_post else '⬜'} Закрепить", f"post_toggle_pin_{pid}")],
            [btn(f"{'✅' if post.has_spoiler else '⬜'} Спойлер", f"post_toggle_spoiler_{pid}")],
            [btn(f"{'✅' if post.has_participate_button else '⬜'} Участвовать", f"post_toggle_part_{pid}")],
            back_btn(f"edit_{pid}")
        ]
        await safe_edit(cb.message, f"⚙️ <b>Настройки поста #{pid}</b>", kb(rows))

    @router.callback_query(F.data.startswith("post_toggle_"))
    async def cb_post_toggle_setting(cb: CallbackQuery):
        parts = cb.data.split("_")
        setting = parts[2]
        pid = int(parts[3])
        post = await db.get_post(pid)
        if not post:
            return await cb.answer("Не найден", show_alert=True)
        if setting == "pin":
            await db.update_post(pid, pin_post=int(not post.pin_post))
        elif setting == "spoiler":
            await db.update_post(pid, has_spoiler=int(not post.has_spoiler))
        elif setting == "part":
            await db.update_post(pid, has_participate_button=int(not post.has_participate_button))
        # Refresh
        post = await db.get_post(pid)
        rows = [
            [btn(f"{'✅' if post.pin_post else '⬜'} Закрепить", f"post_toggle_pin_{pid}")],
            [btn(f"{'✅' if post.has_spoiler else '⬜'} Спойлер", f"post_toggle_spoiler_{pid}")],
            [btn(f"{'✅' if post.has_participate_button else '⬜'} Участвовать", f"post_toggle_part_{pid}")],
            back_btn(f"edit_{pid}")
        ]
        await safe_edit(cb.message, None, kb(rows))
        await cb.answer()

    # ==================== Post Creation Flow ====================

    @router.callback_query(F.data == "new_post")
    async def cb_new_post(cb: CallbackQuery, state: FSMContext):
        chats = await db.get_chats(cb.from_user.id)
        if not chats:
            return await cb.answer("Добавьте бота в чат", show_alert=True)
        await state.clear()
        if len(chats) == 1:
            # Single chat - proceed directly
            await state.update_data(selected_chats=[chats[0].chat_id])
            await _show_content_type(cb.message, safe_edit)
        else:
            # Multiple chats - allow selection
            await state.update_data(selected_chats=[])
            await safe_edit(cb.message, "📝 <b>Выберите чаты:</b>\n💡 Можно выбрать несколько",
                           chats_select_kb(chats, []))
            await state.set_state(S.selecting_chats)

    @router.callback_query(F.data.startswith("sel_chat_"))
    async def cb_select_chat(cb: CallbackQuery, state: FSMContext):
        cid = int(cb.data.split("_")[2])
        data = await state.get_data()
        selected = data.get("selected_chats", [])
        if cid in selected:
            selected.remove(cid)
        else:
            selected.append(cid)
        await state.update_data(selected_chats=selected)
        chats = await db.get_chats(cb.from_user.id)
        await safe_edit(cb.message, None, chats_select_kb(chats, selected))
        await cb.answer()

    @router.callback_query(F.data == "confirm_chats")
    async def cb_confirm_chats(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        selected = data.get("selected_chats", [])
        if not selected:
            return await cb.answer("Выберите хотя бы один чат", show_alert=True)
        await state.set_state(None)
        await _show_content_type(cb.message, safe_edit)

    async def _show_content_type(msg, safe_edit):
        await safe_edit(msg, "📋 <b>Тип контента:</b>", kb([
            [btn("📝 Текст", "type_text"), btn("🖼 Фото", "type_photo")],
            [btn("🎥 Видео", "type_video"), btn("📎 Документ", "type_doc")],
            [btn("❌ Отмена", "cancel")]
        ]))

    @router.callback_query(F.data.startswith("type_"))
    async def cb_content_type(cb: CallbackQuery, state: FSMContext):
        t = cb.data.split("_")[1]
        await state.update_data(content_type=t)
        if t == "text":
            await safe_edit(cb.message, "✍️ <b>Введите текст поста:</b>\n\n💡 Поддерживается HTML разметка")
            await state.set_state(S.content)
        else:
            media_names = {"photo": "фото", "video": "видео", "doc": "документ"}
            await safe_edit(cb.message, f"📎 <b>Отправьте {media_names.get(t, 'медиа')}:</b>")
            await state.set_state(S.media)

    @router.message(S.content, F.chat.type == ChatType.PRIVATE)
    async def on_content(msg: Message, state: FSMContext):
        await state.update_data(content=msg.text or msg.caption or "")
        await msg.answer("⏱ <b>Когда опубликовать?</b>", reply_markup=schedule_kb(), parse_mode=ParseMode.HTML)

    @router.message(S.media, F.chat.type == ChatType.PRIVATE)
    async def on_media(msg: Message, state: FSMContext):
        fid, mt = None, None
        if msg.photo:
            fid, mt = msg.photo[-1].file_id, "photo"
        elif msg.video:
            fid, mt = msg.video.file_id, "video"
        elif msg.document:
            fid, mt = msg.document.file_id, "document"
        if not fid:
            return await msg.answer("❌ Отправьте медиа файл")
        await state.update_data(media_file_id=fid, content_type=mt, media_type=mt)
        await msg.answer("✍️ <b>Подпись (или /skip):</b>", parse_mode=ParseMode.HTML)
        await state.set_state(S.content)

    # ==================== Schedule Selection ====================

    @router.callback_query(F.data == "now")
    async def cb_now(cb: CallbackQuery, state: FSMContext):
        await _publish_now(cb, state, db, bot, scheduler, notify_error, safe_edit)

    @router.callback_query(F.data.startswith("sched_"))
    async def cb_schedule_type(cb: CallbackQuery, state: FSMContext):
        st = cb.data.split("_")[1]
        await state.update_data(schedule_type=st, selected_times=[], selected_days=[])
        
        if st == "once":
            now = datetime.now()
            await safe_edit(cb.message, "📅 <b>Выберите дату:</b>", calendar_kb(now.year, now.month))
        elif st == "daily":
            await safe_edit(cb.message, "⏰ <b>Время:</b>\n💡 Можно выбрать несколько!", time_picker_kb(True))
            await state.update_data(multi_time=True, next_step="config")
        elif st == "weekly":
            await safe_edit(cb.message, "⏰ <b>Время:</b>", time_picker_kb())
            await state.update_data(next_step="days")
        elif st == "monthly":
            await safe_edit(cb.message, "🗓 <b>День месяца:</b>", monthly_day_picker_kb())

    @router.callback_query(F.data.startswith("month_day_"))
    async def cb_month_day(cb: CallbackQuery, state: FSMContext):
        day = int(cb.data.split("_")[2])
        await state.update_data(day_of_month=day, next_step="config")
        await safe_edit(cb.message, f"🗓 <b>{day}-го числа</b>\n\n⏰ Время:", time_picker_kb())

    @router.callback_query(F.data.startswith("cal_"))
    async def cb_calendar(cb: CallbackQuery, state: FSMContext):
        parts = cb.data.split("_")
        if cb.data.startswith("cal_prev") or cb.data.startswith("cal_next"):
            y, m = int(parts[2]), int(parts[3])
            m = m - 1 if "prev" in cb.data else m + 1
            if m < 1:
                m, y = 12, y - 1
            if m > 12:
                m, y = 1, y + 1
            await safe_edit(cb.message, None, calendar_kb(y, m))
        elif cb.data.startswith("cal_day"):
            y, m, day = int(parts[2]), int(parts[3]), int(parts[4])
            await state.update_data(scheduled_date=f"{day:02d}.{m:02d}.{y}", next_step="config")
            await safe_edit(cb.message, f"📅 <b>{day:02d}.{m:02d}.{y}</b>\n\n⏰ Время:", time_picker_kb())

    @router.callback_query(F.data.startswith("time_") & (F.data != "time_manual"))
    async def cb_time_select(cb: CallbackQuery, state: FSMContext):
        parts = cb.data.split("_")
        t = f"{parts[1]}:{parts[2]}"
        data = await state.get_data()
        
        if data.get("multi_time"):
            sel = data.get("selected_times", [])
            if t in sel:
                sel.remove(t)
            else:
                sel.append(t)
            sel.sort()
            await state.update_data(selected_times=sel)
            await safe_edit(cb.message, f"⏰ <b>Выбрано:</b> {', '.join(sel) or 'нет'}", time_picker_kb(True, sel))
        else:
            await state.update_data(scheduled_time=t)
            if data.get("next_step") == "days":
                await state.update_data(selected_days=[])
                await safe_edit(cb.message, f"⏰ {t}\n\n📅 <b>Дни недели:</b>", days_picker_kb([]))
            else:
                await _show_settings(cb.message, state, safe_edit)

    @router.callback_query(F.data == "time_manual")
    async def cb_time_manual(cb: CallbackQuery, state: FSMContext):
        await safe_edit(cb.message, "⏰ <b>Введите время (HH:MM):</b>\n💡 Можно несколько через Enter")
        await state.set_state(S.time)

    @router.message(S.time, F.chat.type == ChatType.PRIVATE)
    async def on_time_input(msg: Message, state: FSMContext):
        data = await state.get_data()
        times = []
        for line in msg.text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                h, m = map(int, line.split(":"))
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
                times.append(f"{h:02d}:{m:02d}")
            except:
                return await msg.answer(f"❌ Ошибка: {line}")
        if not times:
            return await msg.answer("❌ Формат: HH:MM")
        await state.update_data(scheduled_time=",".join(times), multi_time=False)
        if data.get("next_step") == "days":
            await state.update_data(selected_days=[])
            await msg.answer(f"⏰ {times[0]}\n\n📅 <b>Дни:</b>", reply_markup=days_picker_kb([]), parse_mode=ParseMode.HTML)
        else:
            sent = await msg.answer("⏳")
            await _show_settings(sent, state, safe_edit)

    @router.callback_query(F.data == "times_done")
    async def cb_times_done(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        times = data.get("selected_times", [])
        if not times:
            return await cb.answer("Выберите время", show_alert=True)
        await state.update_data(scheduled_time=",".join(times), multi_time=False)
        await _show_settings(cb.message, state, safe_edit)

    @router.callback_query(F.data.startswith("day_toggle_"))
    async def cb_day_toggle(cb: CallbackQuery, state: FSMContext):
        day = int(cb.data.split("_")[2])
        data = await state.get_data()
        sel = data.get("selected_days", [])
        if day in sel:
            sel.remove(day)
        else:
            sel.append(day)
        await state.update_data(selected_days=sel)
        await safe_edit(cb.message, None, days_picker_kb(sel))

    @router.callback_query(F.data == "days_done")
    async def cb_days_done(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        sel = data.get("selected_days", [])
        if not sel:
            return await cb.answer("Выберите дни", show_alert=True)
        await state.update_data(days_of_week=",".join(map(str, sorted(sel))))
        await _show_settings(cb.message, state, safe_edit)

    # ==================== Settings ====================

    async def _show_settings(msg, state: FSMContext, safe_edit):
        data = await state.get_data()
        st = data.get("schedule_type", "once")
        tm = data.get("scheduled_time", "")
        dt = data.get("scheduled_date", "")
        dom = data.get("day_of_month")
        
        info = ""
        if st == "once" and dt:
            info = f"📅 {dt} в {tm}"
        elif st == "daily":
            info = f"🔄 Ежедневно в {tm}"
        elif st == "weekly":
            info = f"📅 Еженедельно в {tm}"
        elif st == "monthly" and dom:
            info = f"🗓 {dom}-го числа в {tm}"
        
        preview = (data.get("content", "")[:50] + "...") if len(data.get("content", "")) > 50 else (data.get("content") or "Медиа")
        text = f"⚙️ <b>Настройки</b>\n\n📝 {preview}\n{info}"
        await state.set_state(S.config)
        try:
            await safe_edit(msg, text, settings_kb(data))
        except:
            await bot.send_message(msg.chat.id, text, reply_markup=settings_kb(data), parse_mode=ParseMode.HTML)

    @router.callback_query(F.data == "toggle_pin")
    async def cb_toggle_pin(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        await state.update_data(pin_post=not data.get("pin_post", False))
        await safe_edit(cb.message, None, settings_kb(await state.get_data()))

    @router.callback_query(F.data == "toggle_spoiler")
    async def cb_toggle_spoiler(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        await state.update_data(has_spoiler=not data.get("has_spoiler", False))
        await safe_edit(cb.message, None, settings_kb(await state.get_data()))

    @router.callback_query(F.data == "toggle_participate")
    async def cb_toggle_participate(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        await state.update_data(has_participate=not data.get("has_participate", False))
        await safe_edit(cb.message, None, settings_kb(await state.get_data()))

    @router.callback_query(F.data == "url_buttons")
    async def cb_url_buttons(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        btns = data.get("url_buttons", [])
        rows = [[btn(f"🗑 {b['text']}", f"rm_url_{i}")] for i, b in enumerate(btns)]
        rows += [[btn("➕ Добавить", "add_url")], back_btn("back_settings")]
        await safe_edit(cb.message, "🔗 <b>URL кнопки:</b>", kb(rows))

    @router.callback_query(F.data == "add_url")
    async def cb_add_url(cb: CallbackQuery, state: FSMContext):
        await safe_edit(cb.message, "🔗 <b>Формат:</b>\n<code>Текст | https://url</code>")
        await state.set_state(S.url_btn)

    @router.message(S.url_btn, F.chat.type == ChatType.PRIVATE)
    async def on_url_btn(msg: Message, state: FSMContext):
        try:
            t, u = [p.strip() for p in msg.text.split("|")]
            if not t or not u.startswith("http"):
                raise ValueError
        except:
            return await msg.answer("❌ Формат: Текст | https://url")
        data = await state.get_data()
        btns = data.get("url_buttons", [])
        btns.append({"text": t, "url": u})
        await state.update_data(url_buttons=btns)
        sent = await msg.answer("✅ Добавлено")
        await _show_settings(sent, state, safe_edit)

    @router.callback_query(F.data.startswith("rm_url_"))
    async def cb_rm_url(cb: CallbackQuery, state: FSMContext):
        i = int(cb.data.split("_")[2])
        data = await state.get_data()
        btns = data.get("url_buttons", [])
        if 0 <= i < len(btns):
            btns.pop(i)
        await state.update_data(url_buttons=btns)
        rows = [[btn(f"🗑 {b['text']}", f"rm_url_{j}")] for j, b in enumerate(btns)]
        rows += [[btn("➕ Добавить", "add_url")], back_btn("back_settings")]
        await safe_edit(cb.message, None, kb(rows))

    @router.callback_query(F.data == "back_settings")
    async def cb_back_settings(cb: CallbackQuery, state: FSMContext):
        await _show_settings(cb.message, state, safe_edit)

    # ==================== Reaction Buttons ====================

    @router.callback_query(F.data == "reaction_buttons")
    async def cb_reaction_buttons(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        btns = data.get("reaction_buttons", [])
        await safe_edit(cb.message, "🗳 <b>Кнопки реакций</b>\n\nДобавьте кнопки для голосования:", 
                       reaction_buttons_kb(btns))

    @router.callback_query(F.data == "add_react_custom")
    async def cb_add_react_custom(cb: CallbackQuery, state: FSMContext):
        await safe_edit(cb.message, 
            "🗳 <b>Введите текст кнопки</b>\n\n"
            "Примеры:\n"
            "<code>👍</code>\n"
            "<code>✅ За</code>\n"
            "<code>❌ Против</code>")
        await state.set_state(S.add_reaction)

    @router.callback_query(F.data == "react_presets")
    async def cb_react_presets(cb: CallbackQuery):
        await safe_edit(cb.message, "📦 <b>Готовые наборы кнопок:</b>", reaction_presets_kb())

    @router.callback_query(F.data.startswith("preset_"))
    async def cb_preset(cb: CallbackQuery, state: FSMContext):
        preset = cb.data.split("_")[1]
        presets = {
            "thumbs": [{"id": "like", "text": "👍"}, {"id": "dislike", "text": "👎"}],
            "vote": [{"id": "yes", "text": "✅ За"}, {"id": "no", "text": "❌ Против"}],
            "emotions": [{"id": "love", "text": "❤️"}, {"id": "laugh", "text": "😂"}, 
                        {"id": "wow", "text": "😮"}, {"id": "sad", "text": "😢"}, {"id": "angry", "text": "😡"}],
            "fire": [{"id": "fire", "text": "🔥"}, {"id": "100", "text": "💯"}, {"id": "clap", "text": "👏"}],
            "numbers": [{"id": "1", "text": "1️⃣"}, {"id": "2", "text": "2️⃣"}, {"id": "3", "text": "3️⃣"}, 
                       {"id": "4", "text": "4️⃣"}, {"id": "5", "text": "5️⃣"}]
        }
        btns = presets.get(preset, [])
        await state.update_data(reaction_buttons=btns)
        await cb.answer(f"✅ Добавлено {len(btns)} кнопок")
        await _show_settings(cb.message, state, safe_edit)

    @router.callback_query(F.data.startswith("rm_react_"))
    async def cb_rm_react(cb: CallbackQuery, state: FSMContext):
        i = int(cb.data.split("_")[2])
        data = await state.get_data()
        btns = data.get("reaction_buttons", [])
        if 0 <= i < len(btns):
            btns.pop(i)
        await state.update_data(reaction_buttons=btns)
        await safe_edit(cb.message, "🗳 <b>Кнопки реакций:</b>", reaction_buttons_kb(btns))

    @router.callback_query(F.data == "add_media")
    async def cb_add_media(cb: CallbackQuery, state: FSMContext):
        await safe_edit(cb.message, "🖼 <b>Отправьте фото/видео:</b>")
        await state.set_state(S.add_media)

    @router.message(S.add_media, F.chat.type == ChatType.PRIVATE)
    async def on_add_media(msg: Message, state: FSMContext):
        fid, mt = None, None
        if msg.photo:
            fid, mt = msg.photo[-1].file_id, "photo"
        elif msg.video:
            fid, mt = msg.video.file_id, "video"
        if fid:
            await state.update_data(media_file_id=fid, media_type=mt, content_type=mt)
            sent = await msg.answer("✅ Медиа добавлено")
            await _show_settings(sent, state, safe_edit)
        else:
            await msg.answer("❌ Отправьте фото или видео")

    @router.message(S.add_reaction, F.chat.type == ChatType.PRIVATE)
    async def on_add_reaction(msg: Message, state: FSMContext):
        text = msg.text.strip()
        if not text:
            return await msg.answer("❌ Введите текст кнопки")
        # Generate unique id
        import hashlib
        btn_id = hashlib.md5(text.encode()).hexdigest()[:8]
        data = await state.get_data()
        btns = data.get("reaction_buttons", [])
        btns.append({"id": btn_id, "text": text})
        await state.update_data(reaction_buttons=btns)
        sent = await msg.answer(f"✅ Кнопка «{text}» добавлена")
        await _show_settings(sent, state, safe_edit)

    @router.callback_query(F.data == "preview")
    async def cb_preview(cb: CallbackQuery, state: FSMContext):
        await _send_state_preview(cb.from_user.id, state, bot)
        await cb.answer()

    @router.callback_query(F.data == "save")
    async def cb_save(cb: CallbackQuery, state: FSMContext):
        await _save_post(cb, state, db, scheduler, bot, notify_error, safe_edit)

    @router.callback_query(F.data == "publish")
    async def cb_publish(cb: CallbackQuery, state: FSMContext):
        await _publish_now(cb, state, db, bot, scheduler, notify_error, safe_edit, with_settings=True)

    @router.callback_query(F.data == "cancel")
    async def cb_cancel(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await safe_edit(cb.message, "❌ <b>Отменено</b>", kb([[btn("📝 Новый пост", "new_post")], back_btn()]))

    # ==================== Participant Button ====================

    @router.callback_query(F.data.startswith("part_"))
    async def cb_participate(cb: CallbackQuery):
        pid = int(cb.data.split("_")[1])
        added = await db.add_participant(pid, cb.from_user.id, cb.from_user.username or cb.from_user.first_name)
        count = await db.count_participants(pid)
        if added:
            await cb.answer(f"✅ Вы участвуете! Всего: {count}", show_alert=True)
        else:
            await cb.answer("Вы уже участвуете!", show_alert=True)
        # Update button count
        await _update_post_buttons(cb, pid, db, safe_edit)

    # ==================== Reaction Buttons (in published post) ====================

    @router.callback_query(F.data.startswith("react_"))
    async def cb_react(cb: CallbackQuery):
        """Handle reaction button click in published post."""
        parts = cb.data.split("_")
        pid = int(parts[1])
        button_id = parts[2]
        uid = cb.from_user.id
        uname = cb.from_user.username or cb.from_user.first_name
        
        # Check if user already reacted to this post
        existing = await db.get_user_reaction(pid, uid)
        
        if existing == button_id:
            # Toggle off - remove reaction
            await db.remove_reaction(pid, button_id, uid)
            await cb.answer("❌ Голос отменён")
        elif existing:
            # Change vote - remove old, add new
            await db.remove_reaction(pid, existing, uid)
            await db.add_reaction(pid, button_id, uid, uname)
            await cb.answer("✅ Голос изменён!")
        else:
            # New vote
            await db.add_reaction(pid, button_id, uid, uname)
            count = await db.count_reactions(pid, button_id)
            await cb.answer(f"✅ Голос принят! ({count})")
        
        # Update buttons
        await _update_post_buttons(cb, pid, db, safe_edit)

    async def _update_post_buttons(cb: CallbackQuery, pid: int, db: Database, safe_edit):
        """Update post buttons after vote/participation."""
        post = await db.get_post(pid)
        if not post:
            return
        part_count = await db.count_participants(pid)
        reaction_counts = await db.get_all_reaction_counts(pid)
        markup = post_kb(
            pid, post.has_participate_button, post.button_text, 
            post.url_buttons, part_count, post.reaction_buttons, reaction_counts
        )
        try:
            await safe_edit(cb.message, None, markup)
        except:
            pass

    # ==================== Helpers ====================

    async def _send_state_preview(uid: int, state: FSMContext, bot: Bot):
        from ..models import ReactionButton
        data = await state.get_data()
        content = data.get("content", "")
        mt = data.get("content_type", "text")
        fid = data.get("media_file_id")
        spoiler = data.get("has_spoiler")
        part = data.get("has_participate")
        url_btns = [UrlButton(**b) for b in data.get("url_buttons", [])]
        reaction_btns = [ReactionButton(**b) for b in data.get("reaction_buttons", [])]
        markup = post_kb(0, part, data.get("button_text", "Участвовать"), url_btns, 0, reaction_btns, {})
        try:
            if mt == "text" or not fid:
                await bot.send_message(uid, content or "(пусто)", parse_mode=ParseMode.HTML, reply_markup=markup)
            elif mt == "photo":
                await bot.send_photo(uid, fid, caption=content, parse_mode=ParseMode.HTML, has_spoiler=spoiler, reply_markup=markup)
            elif mt == "video":
                await bot.send_video(uid, fid, caption=content, parse_mode=ParseMode.HTML, has_spoiler=spoiler, reply_markup=markup)
            else:
                await bot.send_document(uid, fid, caption=content, parse_mode=ParseMode.HTML, reply_markup=markup)
        except Exception as e:
            await bot.send_message(uid, f"❌ Ошибка: {e}")

    async def _send_post_preview(uid: int, post: Post, db: Database, bot: Bot):
        count = await db.count_participants(post.post_id)
        reaction_counts = await db.get_all_reaction_counts(post.post_id)
        markup = post_kb(post.post_id, post.has_participate_button, post.button_text, 
                        post.url_buttons, count, post.reaction_buttons, reaction_counts)
        try:
            if post.media_type == "text" or not post.media_file_id:
                await bot.send_message(uid, post.content, parse_mode=ParseMode.HTML, reply_markup=markup)
            elif post.media_type == "photo":
                await bot.send_photo(uid, post.media_file_id, caption=post.content, parse_mode=ParseMode.HTML,
                                     has_spoiler=post.has_spoiler, reply_markup=markup)
            elif post.media_type == "video":
                await bot.send_video(uid, post.media_file_id, caption=post.content, parse_mode=ParseMode.HTML,
                                     has_spoiler=post.has_spoiler, reply_markup=markup)
        except:
            pass

    async def _save_post(cb: CallbackQuery, state: FSMContext, db: Database, scheduler, bot: Bot, notify_error, safe_edit):
        data = await state.get_data()
        selected_chats = data.get("selected_chats", [])
        if not selected_chats:
            return await cb.answer("Нет выбранных чатов", show_alert=True)
        
        saved_ids = []
        for chat_id in selected_chats:
            pid = await db.add_post(
                chat_id=chat_id, owner_id=cb.from_user.id, content=data.get("content", ""),
                media_type=data.get("content_type"), media_file_id=data.get("media_file_id"),
                schedule_type=data.get("schedule_type", "once"), scheduled_time=data.get("scheduled_time", ""),
                scheduled_date=data.get("scheduled_date"), days_of_week=data.get("days_of_week"),
                day_of_month=data.get("day_of_month"), pin_post=int(data.get("pin_post", 0)),
                has_spoiler=int(data.get("has_spoiler", 0)), has_participate=int(data.get("has_participate", 0)),
                button_text=data.get("button_text", "Участвовать"),
                url_buttons=json.dumps(data.get("url_buttons", [])), template_name=data.get("template_name"),
                reaction_buttons=json.dumps(data.get("reaction_buttons", []))
            )
            saved_ids.append(pid)
            await db.update_stats(cb.from_user.id, created=1)
            await _register_job(pid, db, scheduler, bot, notify_error)
        
        await state.clear()
        if len(saved_ids) == 1:
            text = f"✅ <b>Пост #{saved_ids[0]} сохранён!</b>"
        else:
            text = f"✅ <b>Сохранено {len(saved_ids)} постов!</b>"
        await safe_edit(cb.message, text, kb([[btn("📊 Посты", "posts")], [btn("📝 Новый", "new_post")], back_btn()]))

    async def _publish_now(cb: CallbackQuery, state: FSMContext, db: Database, bot: Bot, scheduler, notify_error, safe_edit, with_settings=False):
        data = await state.get_data()
        selected_chats = data.get("selected_chats", [])
        if not selected_chats:
            return await cb.answer("Нет выбранных чатов", show_alert=True)
        
        success_count = 0
        for chat_id in selected_chats:
            pid = await db.add_post(
                chat_id=chat_id, owner_id=cb.from_user.id, content=data.get("content", ""),
                media_type=data.get("content_type"), media_file_id=data.get("media_file_id"),
                schedule_type="instant", pin_post=int(data.get("pin_post", 0)) if with_settings else 0,
                has_spoiler=int(data.get("has_spoiler", 0)) if with_settings else 0,
                has_participate=int(data.get("has_participate", 0)) if with_settings else 0,
                button_text=data.get("button_text", "Участвовать"),
                url_buttons=json.dumps(data.get("url_buttons", [])) if with_settings else "[]",
                reaction_buttons=json.dumps(data.get("reaction_buttons", [])) if with_settings else "[]"
            )
            sent = await _execute_post(pid, db, bot, notify_error)
            if sent:
                success_count += 1
        
        await state.clear()
        if success_count == len(selected_chats):
            status = "🚀 <b>Опубликовано!</b>"
        elif success_count > 0:
            status = f"⚠️ <b>Частично: {success_count}/{len(selected_chats)}</b>"
        else:
            status = "❌ <b>Ошибка публикации</b>"
        await safe_edit(cb.message, status, kb([[btn("📊 Посты", "posts")], [btn("📝 Новый", "new_post")], back_btn()]))

    async def _execute_post(pid: int, db: Database, bot: Bot, notify_error) -> bool:
        post = await db.get_post(pid)
        if not post:
            return False
        
        count = await db.count_participants(post.post_id)
        reaction_counts = await db.get_all_reaction_counts(post.post_id)
        markup = post_kb(post.post_id, post.has_participate_button, post.button_text, 
                        post.url_buttons, count, post.reaction_buttons, reaction_counts)
        
        try:
            if post.media_type == "text" or not post.media_file_id:
                sent = await bot.send_message(post.chat_id, post.content, parse_mode=ParseMode.HTML, reply_markup=markup)
            elif post.media_type == "photo":
                sent = await bot.send_photo(post.chat_id, post.media_file_id, caption=post.content,
                                           parse_mode=ParseMode.HTML, has_spoiler=post.has_spoiler, reply_markup=markup)
            elif post.media_type == "video":
                sent = await bot.send_video(post.chat_id, post.media_file_id, caption=post.content,
                                           parse_mode=ParseMode.HTML, has_spoiler=post.has_spoiler, reply_markup=markup)
            else:
                sent = await bot.send_document(post.chat_id, post.media_file_id, caption=post.content,
                                              parse_mode=ParseMode.HTML, reply_markup=markup)
            
            await db.update_post(pid, sent_message_id=sent.message_id, 
                                execution_count=post.execution_count + 1,
                                last_sent_at=datetime.now().isoformat())
            await db.update_stats(post.owner_id, sent=1)
            await db.add_history(pid, post.chat_id, sent.message_id, True)
            
            if post.pin_post:
                try:
                    await bot.pin_chat_message(post.chat_id, sent.message_id, disable_notification=True)
                except:
                    pass
            
            if post.schedule_type == "once":
                await db.update_post(pid, is_active=0)
            
            return True
        except Exception as e:
            logger.error(f"Execute post {pid}: {e}")
            await db.update_stats(post.owner_id, failed=1)
            await db.add_history(pid, post.chat_id, 0, False, str(e))
            await notify_error(post.owner_id, pid, str(e))
            return False

    async def _register_job(pid: int, db: Database, scheduler, bot: Bot, notify_error):
        post = await db.get_post(pid)
        if not post or not post.is_active:
            return
        
        tz = pytz.timezone(await db.get_tz(post.owner_id))
        jid = f"post_{pid}"
        
        # Remove existing jobs for this post
        _remove_job(pid, scheduler)
        
        async def execute():
            await _execute_post(pid, db, bot, notify_error)
        
        st = post.schedule_type
        tm = post.scheduled_time
        
        if st == "once" and post.scheduled_date and tm:
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                d, mo, y = map(int, post.scheduled_date.split("."))
                run = tz.localize(datetime(y, mo, d, h, m))
                scheduler.add_job(execute, 'date', run_date=run, id=f"{jid}_{i}", replace_existing=True)
        elif st == "daily" and tm:
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                scheduler.add_job(execute, 'cron', hour=h, minute=m, timezone=tz, id=f"{jid}_{i}", replace_existing=True)
        elif st == "weekly" and tm and post.days_of_week:
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                scheduler.add_job(execute, 'cron', day_of_week=post.days_of_week, hour=h, minute=m,
                                 timezone=tz, id=f"{jid}_{i}", replace_existing=True)
        elif st == "monthly" and tm and post.day_of_month:
            for i, t in enumerate(tm.split(",")):
                h, m = map(int, t.strip().split(":"))
                scheduler.add_job(execute, 'cron', day=post.day_of_month, hour=h, minute=m,
                                 timezone=tz, id=f"{jid}_{i}", replace_existing=True)

    def _remove_job(pid: int, scheduler):
        for suffix in range(10):  # Support up to 10 times per post
            try:
                scheduler.remove_job(f"post_{pid}_{suffix}")
            except:
                pass
        try:
            scheduler.remove_job(f"post_{pid}")
        except:
            pass
