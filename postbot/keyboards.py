"""Keyboard builders for PostBot"""
import os
import calendar
from datetime import datetime
from typing import List, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from .models import Post, Template, Chat, UrlButton, ReactionButton


def kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def back_btn(cb: str = "main") -> List[InlineKeyboardButton]:
    return [btn("🔙 Назад", cb)]


def main_kb() -> InlineKeyboardMarkup:
    rows = [
        [btn("📋 Чаты", "chats")],
        [btn("📝 Создать пост", "new_post")],
        [btn("📊 Посты", "posts")],
        [btn("📅 Контент-план", "plan")],
        [btn("📑 Шаблоны", "templates")],
        [btn("📤 Экспорт / 📥 Импорт", "export_import")]
    ]
    if os.getenv("WEB_PORT"):
        rows.append([btn("🌐 Веб-панель", "web_panel")])
    rows.append([btn("⚙️ Настройки", "settings")])
    return kb(rows)


def schedule_kb() -> InlineKeyboardMarkup:
    return kb([
        [btn("🚀 Сейчас", "now")],
        [btn("⏰ Один раз", "sched_once"), btn("🔄 Ежедневно", "sched_daily")],
        [btn("📅 Еженедельно", "sched_weekly"), btn("🗓 Ежемесячно", "sched_monthly")],
        [btn("❌ Отмена", "cancel")]
    ])


def settings_kb(data: dict) -> InlineKeyboardMarkup:
    pin = data.get("pin_post")
    spoiler = data.get("has_spoiler")
    part = data.get("has_participate")
    media = data.get("content_type") in ("photo", "video") or data.get("media_file_id")
    reaction_btns = data.get("reaction_buttons", [])
    
    rows = [[btn(f"{'✅' if pin else '⬜'} Закрепить", "toggle_pin")]]
    if media:
        rows.append([btn(f"{'✅' if spoiler else '⬜'} Спойлер", "toggle_spoiler")])
    rows.append([btn(f"{'✅' if part else '⬜'} Участвовать", "toggle_participate")])
    rows.append([btn(f"🔗 URL кнопки ({len(data.get('url_buttons', []))})", "url_buttons")])
    rows.append([btn(f"🗳 Кнопки реакций ({len(reaction_btns)})", "reaction_buttons")])
    if not media:
        rows.append([btn("🖼 Добавить медиа", "add_media")])
    rows.append([btn("📑 Из шаблона", "from_template")])
    rows.append([btn("👁 Превью", "preview"), btn("✅ Сохранить", "save")])
    rows.append([btn("🚀 Опубликовать", "publish"), btn("💾 Как шаблон", "save_template")])
    rows.append([btn("❌ Отмена", "cancel")])
    return kb(rows)


def post_kb(post_id: int, has_participate: bool, button_text: str, 
            url_buttons: List[UrlButton], participant_count: int,
            reaction_buttons: List[ReactionButton] = None,
            reaction_counts: dict = None) -> Optional[InlineKeyboardMarkup]:
    """Build post keyboard with URL buttons, participate button, and reaction buttons."""
    rows = []
    # URL buttons
    for b in url_buttons:
        if b.text and b.url:
            rows.append([url_btn(b.text, b.url)])
    # Reaction buttons in a row
    if reaction_buttons:
        counts = reaction_counts or {}
        reaction_row = []
        for rb in reaction_buttons:
            count = counts.get(rb.id, 0)
            text = f"{rb.text} ({count})" if count > 0 else rb.text
            reaction_row.append(btn(text, f"react_{post_id}_{rb.id}"))
        if reaction_row:
            rows.append(reaction_row)
    # Participate button
    if has_participate:
        rows.append([btn(f"{button_text} ({participant_count})", f"part_{post_id}")])
    return kb(rows) if rows else None


def post_manage_kb(post: Post) -> InlineKeyboardMarkup:
    return kb([
        [btn("👁 Превью", f"view_{post.post_id}")],
        [btn("✏️ Редактировать", f"edit_{post.post_id}")],
        [btn("📋 Дублировать", f"dup_{post.post_id}")],
        [btn("❌ Откл" if post.is_active else "✅ Вкл", f"toggle_{post.post_id}")],
        [btn("👥 Участники", f"participants_{post.post_id}")] if post.has_participate_button else [],
        [btn("🗑 Удалить", f"del_{post.post_id}")],
        back_btn("posts")
    ])


def post_edit_kb(post_id: int) -> InlineKeyboardMarkup:
    return kb([
        [btn("📝 Текст", f"edit_content_{post_id}")],
        [btn("🖼 Медиа", f"edit_media_{post_id}")],
        [btn("⏰ Время", f"edit_time_{post_id}")],
        [btn("🔗 Кнопки", f"edit_urls_{post_id}")],
        [btn("📌 Настройки", f"edit_settings_{post_id}")],
        back_btn(f"post_{post_id}")
    ])


def posts_filter_kb(current_filter: str = "all") -> InlineKeyboardMarkup:
    filters = [
        ("all", "📊 Все"),
        ("active", "✅ Активные"),
        ("inactive", "❌ Неактивные"),
    ]
    rows = [[btn(f"{'▸ ' if f == current_filter else ''}{name}", f"filter_{f}") for f, name in filters]]
    rows.append([btn("🗑 Удалить все", "bulk_delete"), btn("❌ Откл все", "bulk_disable")])
    rows.append(back_btn())
    return kb(rows)


def chats_select_kb(chats: List[Chat], selected: List[int]) -> InlineKeyboardMarkup:
    rows = []
    for c in chats:
        icon = "📢" if c.chat_type == "channel" else "👥"
        check = "✅" if c.chat_id in selected else "⬜"
        rows.append([btn(f"{check} {icon} {c.chat_title}", f"sel_chat_{c.chat_id}")])
    rows.append([btn("✅ Подтвердить", "confirm_chats")])
    rows.append(back_btn())
    return kb(rows)


def pagination_kb(current_page: int, total_pages: int, prefix: str) -> List[InlineKeyboardButton]:
    btns = []
    if current_page > 0:
        btns.append(btn("◀️", f"{prefix}_page_{current_page - 1}"))
    btns.append(btn(f"{current_page + 1}/{total_pages}", "x"))
    if current_page < total_pages - 1:
        btns.append(btn("▶️", f"{prefix}_page_{current_page + 1}"))
    return btns


def calendar_kb(year: int, month: int) -> InlineKeyboardMarkup:
    names = ["", "Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
    rows = [[btn("◀️", f"cal_prev_{year}_{month}"), btn(f"{names[month]} {year}", "x"), btn("▶️", f"cal_next_{year}_{month}")]]
    rows.append([btn(d, "x") for d in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]])
    today = datetime.now().date()
    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(btn(" ", "x"))
            elif datetime(year, month, day).date() < today:
                row.append(btn("·", "x"))
            else:
                row.append(btn(str(day), f"cal_day_{year}_{month}_{day}"))
        rows.append(row)
    rows.append([btn("❌ Отмена", "cancel")])
    return kb(rows)


def time_picker_kb(multi: bool = False, selected: Optional[List[str]] = None) -> InlineKeyboardMarkup:
    selected = selected or []
    hours = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]
    rows = []
    for i in range(0, len(hours), 4):
        row = [btn(f"{'✅ ' if f'{h:02d}:00' in selected else ''}{h:02d}:00", f"time_{h:02d}_00") 
               for h in hours[i:i+4]]
        rows.append(row)
    rows.append([btn("⌨️ Вручную", "time_manual")])
    if multi and selected:
        rows.append([btn(f"✅ Готово ({len(selected)})", "times_done")])
    rows.append([btn("❌ Отмена", "cancel")])
    return kb(rows)


def days_picker_kb(selected: List[int]) -> InlineKeyboardMarkup:
    names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    r1 = [btn(f"{'✅' if i in selected else ''}{names[i]}", f"day_toggle_{i}") for i in range(4)]
    r2 = [btn(f"{'✅' if i in selected else ''}{names[i]}", f"day_toggle_{i}") for i in range(4, 7)]
    return kb([r1, r2, [btn("✅ Готово", "days_done")], [btn("❌ Отмена", "cancel")]])


def monthly_day_picker_kb(selected: Optional[int] = None) -> InlineKeyboardMarkup:
    rows = []
    for start in range(1, 32, 7):
        row = []
        for day in range(start, min(start + 7, 32)):
            check = "✅" if day == selected else ""
            row.append(btn(f"{check}{day}", f"month_day_{day}"))
        rows.append(row)
    rows.append([btn("❌ Отмена", "cancel")])
    return kb(rows)


def confirm_kb(action: str) -> InlineKeyboardMarkup:
    return kb([
        [btn("✅ Да, подтверждаю", f"confirm_{action}")],
        [btn("❌ Отмена", "cancel")]
    ])


def reaction_buttons_kb(buttons: list, back_cb: str = "back_settings") -> InlineKeyboardMarkup:
    """Keyboard for managing reaction buttons."""
    rows = []
    for i, b in enumerate(buttons):
        rows.append([btn(f"🗑 {b.get('text', b.get('id', '?'))}", f"rm_react_{i}")])
    rows.append([btn("➕ Добавить свою", "add_react_custom")])
    rows.append([btn("📦 Готовые наборы", "react_presets")])
    rows.append(back_btn(back_cb))
    return kb(rows)


def reaction_presets_kb() -> InlineKeyboardMarkup:
    """Preset reaction button sets."""
    return kb([
        [btn("👍 / 👎", "preset_thumbs")],
        [btn("✅ За / ❌ Против", "preset_vote")],
        [btn("❤️ / 😂 / 😮 / 😢 / 😡", "preset_emotions")],
        [btn("🔥 / 💯 / 👏", "preset_fire")],
        [btn("1️⃣ / 2️⃣ / 3️⃣ / 4️⃣ / 5️⃣", "preset_numbers")],
        back_btn("reaction_buttons")
    ])


def templates_kb(templates: List[Template]) -> InlineKeyboardMarkup:
    rows = [[btn(f"📑 {t.name}", f"tpl_{t.template_id}")] for t in templates]
    rows.append([btn("➕ Создать шаблон", "new_template")])
    rows.append(back_btn())
    return kb(rows)


def tz_kb() -> InlineKeyboardMarkup:
    tzs = [
        ("Asia/Jerusalem", "🇮🇱 Иерусалим"),
        ("Europe/Moscow", "🇷🇺 Москва"),
        ("Europe/Kiev", "🇺🇦 Киев"),
        ("Europe/Minsk", "🇧🇾 Минск"),
        ("Asia/Almaty", "🇰🇿 Алматы"),
        ("UTC", "🌍 UTC")
    ]
    return kb([[btn(name, f"tz_{tz}")] for tz, name in tzs] + [back_btn("settings")])
