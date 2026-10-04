from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from i18n import t


def language_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="lang:uz"),
         InlineKeyboardButton("🇬🇧 English", callback_data="lang:en")]
    ])


def main_menu_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(t(lang, "menu_btn_contact_admin"))],
            [KeyboardButton(t(lang, "menu_btn_info"))],
        ],
        resize_keyboard=True,
    )


def cancel_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(t(lang, "menu_btn_cancel"))]], resize_keyboard=True
    )


def collecting_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "collecting_done_btn"), callback_data="collect:done"),
         InlineKeyboardButton(t(lang, "collecting_cancel_btn"), callback_data="collect:cancel")]
    ])


def force_sub_kb(channels: list[dict], lang: str) -> InlineKeyboardMarkup:
    """`channels` -- required_channels jadvalidan qatorlar ro'yxati.
    Haqiqiy Telegram kanallar avval, tashqi havolalar OXIRIDA chiqadi
    (chaqiruvchi tomonda shu tartibda beriladi)."""
    rows = []
    for idx, ch in enumerate(channels, start=1):
        channel_id = str(ch["channel_id"])
        if channel_id.startswith("ext:"):
            url = channel_id[len("ext:"):]
        elif channel_id.startswith("@"):
            url = f"https://t.me/{channel_id.lstrip('@')}"
        elif channel_id.lstrip("-").isdigit():
            url = None  # private channel invite link unknown; fall back to title
        else:
            url = f"https://t.me/{channel_id}"
        label = ch.get("title") or t(lang, "force_sub_channel_btn", num=idx)
        if url:
            rows.append([InlineKeyboardButton(f"📢 {label}", url=url)])
    rows.append([InlineKeyboardButton(t(lang, "force_sub_check_btn"), callback_data="force_sub:check")])
    return InlineKeyboardMarkup(rows)


def admin_panel_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "admin_btn_stats"), callback_data="adm:stats")],
        [InlineKeyboardButton(t(lang, "admin_btn_users"), callback_data="adm:users")],
        [InlineKeyboardButton(t(lang, "admin_btn_top_creators"), callback_data="adm:top_creators"),
         InlineKeyboardButton(t(lang, "admin_btn_top_readers"), callback_data="adm:top_readers")],
        [InlineKeyboardButton(t(lang, "admin_btn_weekly"), callback_data="adm:weekly")],
        [InlineKeyboardButton(t(lang, "admin_btn_limits"), callback_data="adm:limits")],
        [InlineKeyboardButton(t(lang, "admin_btn_channels"), callback_data="adm:channels")],
        [InlineKeyboardButton(t(lang, "admin_btn_broadcast"), callback_data="adm:broadcast")],
        [InlineKeyboardButton(t(lang, "admin_btn_db_check"), callback_data="adm:db_check")],
        [InlineKeyboardButton(t(lang, "admin_btn_memory"), callback_data="adm:memory")],
    ])


def admin_back_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "admin_btn_back"), callback_data="adm:panel")]])


def admin_user_card_kb(lang: str, telegram_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    ban_btn = (
        InlineKeyboardButton(t(lang, "admin_btn_unban"), callback_data=f"adm:unban:{telegram_id}")
        if is_banned else
        InlineKeyboardButton(t(lang, "admin_btn_ban"), callback_data=f"adm:ban:{telegram_id}")
    )
    return InlineKeyboardMarkup([
        [ban_btn, InlineKeyboardButton(t(lang, "admin_btn_message_user"), callback_data=f"adm:msg:{telegram_id}")],
        [InlineKeyboardButton(t(lang, "admin_btn_limit_create"), callback_data=f"adm:ulimit:create:{telegram_id}"),
         InlineKeyboardButton(t(lang, "admin_btn_limit_read"), callback_data=f"adm:ulimit:read:{telegram_id}")],
        [InlineKeyboardButton(t(lang, "admin_btn_back"), callback_data="adm:panel")],
    ])


def admin_limits_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "admin_btn_set_create_limit"), callback_data="adm:glimit:create")],
        [InlineKeyboardButton(t(lang, "admin_btn_set_read_limit"), callback_data="adm:glimit:read")],
        [InlineKeyboardButton(t(lang, "admin_btn_set_max_images"), callback_data="adm:glimit:max_images")],
        [InlineKeyboardButton(t(lang, "admin_btn_back"), callback_data="adm:panel")],
    ])


def admin_channels_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "admin_btn_add_channel"), callback_data="adm:chan_add")],
        [InlineKeyboardButton(t(lang, "admin_btn_remove_channel"), callback_data="adm:chan_remove")],
        [InlineKeyboardButton(t(lang, "admin_btn_back"), callback_data="adm:panel")],
    ])


def admin_memory_kb(lang: str, has_files: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_files:
        rows.append([InlineKeyboardButton(t(lang, "admin_btn_clear_all_memory"), callback_data="adm:mem_clear_all")])
    rows.append([InlineKeyboardButton(t(lang, "admin_btn_back"), callback_data="adm:panel")])
    return InlineKeyboardMarkup(rows)


def admin_reply_kb(lang: str, telegram_id: int) -> InlineKeyboardMarkup:
    """Admin tomoniga yuboriladigan 'foydalanuvchidan yangi xabar'
    bildirishnomasi ostidagi tugma -- bosilsa javob yozish rejimiga
    o'tadi."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "admin_btn_message_user"), callback_data=f"adm:msg:{telegram_id}")]
    ])
