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


def main_menu_kb(lang: str, is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(t(lang, "menu_btn_profile")), KeyboardButton(t(lang, "menu_btn_stats"))],
        [KeyboardButton(t(lang, "menu_btn_contact_admin")), KeyboardButton(t(lang, "menu_btn_language"))],
        [KeyboardButton(t(lang, "menu_btn_info")), KeyboardButton(t(lang, "menu_btn_premium"))],
    ]
    if is_admin:
        rows.append([KeyboardButton(t(lang, "menu_btn_admin_panel"))])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def premium_ad_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "premium_btn_test"), callback_data="premium:info")]
    ])


def cancel_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(t(lang, "menu_btn_cancel"))]], resize_keyboard=True
    )


def collecting_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "collecting_done_btn"), callback_data="collect:done"),
         InlineKeyboardButton(t(lang, "collecting_cancel_btn"), callback_data="collect:cancel")]
    ])


def channel_ref_and_url(channel_id: str) -> tuple[str, str | None]:
    """`channel_id` ustunidagi qiymatni ikkiga ajratadi:
    - `ref` -- a'zolikni TEKSHIRISH uchun (chat_id yoki @username)
    - `url` -- tugmada ko'rsatiladigan HAVOLA (bo'lmasa None)

    Qo'llab-quvvatlanadigan formatlar:
    - "ext:<havola>"              -- tashqi havola (ref == url == havola)
    - "<chat_id>|<invite_link>"   -- private kanal (admin_panel shunday saqlaydi)
    - "@username"                 -- public kanal, username bilan
    - "<chat_id>"                 -- havolasiz (eski yozuvlar -- tugma chiqmaydi)
    """
    if channel_id.startswith("ext:"):
        url = channel_id[len("ext:"):]
        return url, url
    if "|" in channel_id:
        ref, invite_link = channel_id.split("|", 1)
        return ref, (invite_link or None)
    if channel_id.startswith("@"):
        return channel_id, f"https://t.me/{channel_id.lstrip('@')}"
    if channel_id.lstrip("-").isdigit():
        return channel_id, None
    return channel_id, f"https://t.me/{channel_id}"


def force_sub_kb(channels: list[dict], lang: str) -> InlineKeyboardMarkup:
    """`channels` -- required_channels jadvalidan qatorlar ro'yxati.
    Haqiqiy Telegram kanallar avval, tashqi havolalar OXIRIDA chiqadi
    (chaqiruvchi tomonda shu tartibda beriladi). Tugma nomi HAR DOIM
    "Kanal 1", "Kanal 2"... -- admin kiritgan haqiqiy nom hech qachon
    foydalanuvchiga ko'rsatilmaydi."""
    rows = []
    for idx, ch in enumerate(channels, start=1):
        _, url = channel_ref_and_url(str(ch["channel_id"]))
        label = t(lang, "force_sub_channel_btn", num=idx)
        if url:
            rows.append([InlineKeyboardButton(f"📢 {label}", url=url)])
        else:
            # Havola topilmagan (masalan bot admin bo'lmagan private
            # kanal) -- bosilganda hech narsa qilmaydigan tugma.
            rows.append([InlineKeyboardButton(f"📢 {label}", callback_data="force_sub:noop")])
    rows.append([InlineKeyboardButton(t(lang, "force_sub_check_btn"), callback_data="force_sub:check")])
    return InlineKeyboardMarkup(rows)


def admin_panel_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "admin_btn_stats"), callback_data="adm:stats")],
        [InlineKeyboardButton(t(lang, "admin_btn_search"), callback_data="adm:search"),
         InlineKeyboardButton(t(lang, "admin_btn_users"), callback_data="adm:users:0")],
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


def admin_users_list_kb(lang: str, users: list[dict], offset: int, total: int, limit: int = 10) -> InlineKeyboardMarkup:
    """Foydalanuvchilar ro'yxati -- har biri alohida tugma (bosilsa
    kartochkasi ochiladi), pastda oldingi/keyingi sahifa va orqaga."""
    rows = []
    for u in users:
        uname = u.get("username")
        label = f"@{uname}" if uname else (u.get("first_name") or str(u["telegram_id"]))
        if u.get("is_banned"):
            label = f"🚫 {label}"
        rows.append([InlineKeyboardButton(label, callback_data=f"adm:ucard:{u['telegram_id']}:{offset}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"adm:users:{max(0, offset - limit)}"))
    if offset + limit < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"adm:users:{offset + limit}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(t(lang, "admin_btn_back"), callback_data="adm:panel")])
    return InlineKeyboardMarkup(rows)


def admin_user_card_kb(lang: str, telegram_id: int, is_banned: bool, back_offset: int | None = None) -> InlineKeyboardMarkup:
    """`back_offset` berilsa -- 'Orqaga' tugmasi ro'yxatning O'SHA
    sahifasiga qaytaradi (qidiruv natijasidan kelingan bo'lsa esa,
    `back_offset=None`, oddiy admin panelga qaytadi)."""
    ban_btn = (
        InlineKeyboardButton(t(lang, "admin_btn_unban"), callback_data=f"adm:unban:{telegram_id}")
        if is_banned else
        InlineKeyboardButton(t(lang, "admin_btn_ban"), callback_data=f"adm:ban:{telegram_id}")
    )
    back_cb = f"adm:users:{back_offset}" if back_offset is not None else "adm:panel"
    return InlineKeyboardMarkup([
        [ban_btn, InlineKeyboardButton(t(lang, "admin_btn_message_user"), callback_data=f"adm:msg:{telegram_id}")],
        [InlineKeyboardButton(t(lang, "admin_btn_limit_create"), callback_data=f"adm:ulimit:create:{telegram_id}"),
         InlineKeyboardButton(t(lang, "admin_btn_limit_read"), callback_data=f"adm:ulimit:read:{telegram_id}")],
        [InlineKeyboardButton(t(lang, "admin_btn_limit_max_images"), callback_data=f"adm:ulimit:max_images:{telegram_id}")],
        [InlineKeyboardButton(t(lang, "admin_btn_back"), callback_data=back_cb)],
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
        [InlineKeyboardButton(t(lang, "admin_btn_add_ext_link"), callback_data="adm:chan_add_ext")],
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
