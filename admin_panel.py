# ════════════════════════════════════════════════════════════
#  ADMIN PANEL
# ════════════════════════════════════════════════════════════
import asyncio
import os
import urllib.parse

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.errors import RPCError

import config
import database
from i18n import t
import keyboards
import handlers  # is_admin, admin_flow, admin_reply_target, _lang


async def _lang(uid: int) -> str:
    return await database.get_user_language(uid)


def _mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):,.1f}"


# ════════════════════════════════════════════════════════════
#  ADMIN TEXT-KIRITISH ROUTERI -- handlers.py'dagi matn handleridan
#  chaqiriladi. True qaytarsa -- xabar "ishlatildi" deb hisoblanadi.
# ════════════════════════════════════════════════════════════
async def handle_admin_text(client: Client, message: Message) -> bool:
    uid = message.from_user.id
    flow = handlers.admin_flow.get(uid)
    if not flow:
        return False

    action = flow.get("action")
    lang = await _lang(uid)
    text = (message.text or "").strip()

    if action == "search_user":
        handlers.admin_flow.pop(uid, None)
        await _do_user_search(client, message.chat.id, lang, text)
        return True

    if action == "set_user_limit":
        handlers.admin_flow.pop(uid, None)
        try:
            value = max(0, int(text))
        except ValueError:
            return True
        target_uid = flow["target_uid"]
        kind = flow["kind"]
        await database.set_user_limit(target_uid, kind, value)
        await message.reply(t(lang, "admin_limit_set", value=value))
        await _show_user_card(client, message.chat.id, lang, target_uid)
        return True

    if action == "set_global_limit":
        handlers.admin_flow.pop(uid, None)
        try:
            value = max(0, int(text))
        except ValueError:
            return True
        field = flow["field"]
        await database.set_global_limit(field, value)
        if field in ("default_create_limit", "default_read_limit"):
            kind = "create" if field == "default_create_limit" else "read"
            await database.apply_limit_to_all(kind, value)
        await message.reply(t(lang, "admin_global_limit_set", value=value))
        await _show_limits(client, message.chat.id, lang)
        return True

    if action == "add_channel":
        handlers.admin_flow.pop(uid, None)
        await _do_add_channel(client, message, lang)
        return True

    if action == "remove_channel":
        handlers.admin_flow.pop(uid, None)
        try:
            row_id = int(text)
        except ValueError:
            return True
        await database.remove_required_channel(row_id)
        await message.reply(t(lang, "admin_channel_removed"))
        await _show_channels(client, message.chat.id, lang)
        return True

    if action == "broadcast":
        handlers.admin_flow.pop(uid, None)
        await _do_broadcast(client, message, lang)
        return True

    if action == "message_user":
        handlers.admin_flow.pop(uid, None)
        target_uid = flow["target_uid"]
        await _send_admin_message_to_user(client, target_uid, message, lang)
        return True

    return False


async def handle_admin_reply_text(client: Client, message: Message) -> bool:
    """Admin 'Javob yozish' tugmasidan so'ng oddiy xabar yozganda."""
    uid = message.from_user.id
    target_uid = handlers.admin_reply_target.pop(uid, None)
    if not target_uid:
        return False
    lang = await _lang(uid)
    await _send_admin_message_to_user(client, target_uid, message, lang)
    return True


async def _send_admin_message_to_user(client: Client, target_uid: int, message: Message, admin_lang: str):
    target_lang = await _lang(target_uid)
    try:
        await client.send_message(target_uid, t(target_lang, "admin_reply_prefix"))
        await message.copy(target_uid)
        await message.reply(t(admin_lang, "admin_message_sent"))
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
#  PANEL BOSH SAHIFASI
# ════════════════════════════════════════════════════════════
async def show_panel(client: Client, chat_id: int, lang: str, edit_message_id: int | None = None):
    text = t(lang, "admin_panel_title")
    kb = keyboards.admin_panel_kb(lang)
    if edit_message_id:
        try:
            await client.edit_message_text(chat_id, edit_message_id, text, reply_markup=kb)
            return
        except Exception:
            pass
    await client.send_message(chat_id, text, reply_markup=kb)


# ════════════════════════════════════════════════════════════
#  STATISTIKA
# ════════════════════════════════════════════════════════════
async def _show_stats(client: Client, chat_id: int, lang: str, msg_id: int):
    s = await database.get_overview_stats()
    text = t(
        lang, "admin_stats_text",
        total_users=s["total_users"], today_new=s["today_new"],
        today_pdfs=s["today_pdfs"], total_pdfs=s["total_pdfs"],
        total_read=s["total_read"], total_mb=_mb(s["total_bytes"]),
        lang_uz=s["lang_uz"], lang_en=s["lang_en"],
    )
    await client.edit_message_text(chat_id, msg_id, text, reply_markup=keyboards.admin_back_kb(lang))


async def _show_weekly(client: Client, chat_id: int, lang: str, msg_id: int):
    days = await database.get_last_n_days_stats(7)
    if not days:
        rows_text = "—"
    else:
        rows_text = "\n".join(
            t(lang, "admin_weekly_row", day=d["day"], new_users=d["new_users"],
              pdfs_created=d["pdfs_created"], pdfs_read=d["pdfs_read"], images_sent=d["images_sent"])
            for d in days
        )
    text = t(lang, "admin_weekly_text", rows=rows_text)
    await client.edit_message_text(chat_id, msg_id, text, reply_markup=keyboards.admin_back_kb(lang))


async def _show_top_creators(client: Client, chat_id: int, lang: str, msg_id: int):
    rows = await database.get_top_creators(10)
    lines = [f"{i+1}. {_fmt_user(r)} — {r['pdfs_created']}" for i, r in enumerate(rows)] or ["—"]
    await client.edit_message_text(chat_id, msg_id, t(lang, "admin_btn_top_creators") + "\n\n" + "\n".join(lines),
                                    reply_markup=keyboards.admin_back_kb(lang))


async def _show_top_readers(client: Client, chat_id: int, lang: str, msg_id: int):
    rows = await database.get_top_readers(10)
    lines = [f"{i+1}. {_fmt_user(r)} — {r['pdfs_read']}" for i, r in enumerate(rows)] or ["—"]
    await client.edit_message_text(chat_id, msg_id, t(lang, "admin_btn_top_readers") + "\n\n" + "\n".join(lines),
                                    reply_markup=keyboards.admin_back_kb(lang))


def _fmt_user(r: dict) -> str:
    uname = r.get("username")
    return f"@{uname}" if uname else (r.get("first_name") or str(r["telegram_id"]))


# ════════════════════════════════════════════════════════════
#  FOYDALANUVCHI QIDIRISH / KARTOCHKA
# ════════════════════════════════════════════════════════════
async def _ask_search(client: Client, chat_id: int, lang: str, admin_uid: int, msg_id: int):
    handlers.admin_flow[admin_uid] = {"action": "search_user"}
    await client.edit_message_text(chat_id, msg_id, t(lang, "admin_search_prompt"))


async def _do_user_search(client: Client, chat_id: int, lang: str, query: str):
    results = await database.search_users(query, limit=5)
    if not results:
        await client.send_message(chat_id, t(lang, "admin_search_not_found"))
        return
    for r in results:
        await _show_user_card(client, chat_id, lang, r["telegram_id"])


async def _show_user_card(client: Client, chat_id: int, lang: str, target_uid: int, edit_msg_id: int | None = None):
    user = await database.get_user(target_uid)
    if not user:
        await client.send_message(chat_id, t(lang, "admin_search_not_found"))
        return
    status = "🚫 BAN" if user.get("is_banned") else "✅"
    text = t(
        lang, "admin_user_card",
        id=target_uid, username=user.get("username") or "—", name=user.get("first_name") or "—",
        lang=user.get("language") or "—", joined=user.get("joined_at") or "—", status=status,
        images_sent=user.get("images_sent") or 0,
        pdfs_created=user.get("pdfs_created") or 0, pdfs_created_today=user.get("pdfs_created_today") or 0,
        pdfs_read=user.get("pdfs_read") or 0, pdfs_read_today=user.get("pdfs_read_today") or 0,
        create_limit=user.get("daily_create_limit") or 0, read_limit=user.get("daily_read_limit") or 0,
    )
    kb = keyboards.admin_user_card_kb(lang, target_uid, bool(user.get("is_banned")))
    if edit_msg_id:
        try:
            await client.edit_message_text(chat_id, edit_msg_id, text, reply_markup=kb)
            return
        except Exception:
            pass
    await client.send_message(chat_id, text, reply_markup=kb)


# ════════════════════════════════════════════════════════════
#  HAMMAGA LIMITLAR
# ════════════════════════════════════════════════════════════
async def _show_limits(client: Client, chat_id: int, lang: str, msg_id: int | None = None):
    s = await database.get_settings()
    text = t(
        lang, "admin_limits_text",
        create_limit=s["default_create_limit"], read_limit=s["default_read_limit"],
        max_images=s["max_images_per_pdf"],
    )
    kb = keyboards.admin_limits_kb(lang)
    if msg_id:
        await client.edit_message_text(chat_id, msg_id, text, reply_markup=kb)
    else:
        await client.send_message(chat_id, text, reply_markup=kb)


# ════════════════════════════════════════════════════════════
#  MAJBURIY OBUNA KANALLARI
# ════════════════════════════════════════════════════════════
async def _show_channels(client: Client, chat_id: int, lang: str, msg_id: int | None = None):
    channels = await database.list_active_required_channels()
    if not channels:
        list_text = t(lang, "admin_channels_empty")
    else:
        rows = []
        for i, ch in enumerate(channels, 1):
            count = await database.count_channel_members(ch["id"])
            rows.append(f"[{ch['id']}] " + t(lang, "admin_channel_row", num=i, title=ch["title"] or ch["channel_id"], count=count))
        list_text = "\n".join(rows)
    text = t(lang, "admin_channels_text", list=list_text)
    kb = keyboards.admin_channels_kb(lang)
    if msg_id:
        await client.edit_message_text(chat_id, msg_id, text, reply_markup=kb)
    else:
        await client.send_message(chat_id, text, reply_markup=kb)


def _is_telegram_url(text: str) -> bool:
    text = text.strip()
    if text.startswith("@"):
        return True
    lowered = text.lower()
    return any(d in lowered for d in ("t.me/", "telegram.me/", "telegram.dog/"))


def _auto_title_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    parts = [p for p in parsed.path.split("/") if p]
    return f"{parsed.netloc}/{parts[0]}" if parts else parsed.netloc


async def _do_add_channel(client: Client, message: Message, lang: str):
    text = (message.text or "").strip()
    if "|" in text:
        title, url = [x.strip() for x in text.split("|", 1)]
    elif not _is_telegram_url(text):
        title, url = _auto_title_from_url(text), text
    else:
        title, url = None, text

    if title is not None:
        # Tashqi havola
        await database.add_required_channel(f"ext:{url}", title)
        await message.reply(t(lang, "admin_channel_added"))
        await _show_channels(client, message.chat.id, lang)
        return

    # Haqiqiy Telegram kanal -- tekshiramiz
    ref = url
    if ref.startswith("https://t.me/") or ref.startswith("http://t.me/"):
        ref = "@" + ref.split("t.me/")[-1].lstrip("@")
    try:
        chat = await client.get_chat(ref)
        await database.add_required_channel(str(chat.id), chat.title or ref)
        await message.reply(t(lang, "admin_channel_added"))
    except Exception:
        await message.reply(t(lang, "admin_channel_add_error"))
    await _show_channels(client, message.chat.id, lang)


# ════════════════════════════════════════════════════════════
#  XOTIRA
# ════════════════════════════════════════════════════════════
async def _show_memory(client: Client, chat_id: int, lang: str, msg_id: int | None = None):
    rows = await database.list_pending_files_summary()
    total_count = sum(r["file_count"] for r in rows)
    total_size = sum(r["total_size"] for r in rows)
    if not rows:
        list_text = t(lang, "admin_memory_empty")
    else:
        list_text = "\n".join(
            t(lang, "admin_memory_row", id=r["telegram_id"], count=r["file_count"], mb=_mb(r["total_size"]))
            for r in rows
        )
    text = t(lang, "admin_memory_text", list=list_text, total_count=total_count, total_mb=_mb(total_size))
    kb = keyboards.admin_memory_kb(lang, has_files=bool(rows))
    if msg_id:
        await client.edit_message_text(chat_id, msg_id, text, reply_markup=kb)
    else:
        await client.send_message(chat_id, text, reply_markup=kb)


async def _clear_all_memory(client: Client, chat_id: int, lang: str):
    paths = await database.clear_all_pending_files()
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    # Foydalanuvchilarning in-memory holatini ham tozalaymiz (bot
    # xotirasidagi "jarayon" holati)
    for uid in list(handlers.collect_state.keys()):
        await handlers._cancel_collecting(uid)
    await client.send_message(chat_id, t(lang, "admin_memory_cleared"))


# ════════════════════════════════════════════════════════════
#  BROADCAST
# ════════════════════════════════════════════════════════════
async def _do_broadcast(client: Client, message: Message, lang: str):
    user_ids = await database.list_all_user_ids()
    total = len(user_ids)
    status = await message.reply(t(lang, "admin_broadcast_sending", sent=0, total=total))
    sent, failed = 0, 0
    for i, uid in enumerate(user_ids, 1):
        try:
            await message.copy(uid)
            sent += 1
        except Exception:
            failed += 1
        if i % 25 == 0:
            try:
                await status.edit_text(t(lang, "admin_broadcast_sending", sent=sent, total=total))
            except Exception:
                pass
            await asyncio.sleep(0.05)
    await status.edit_text(t(lang, "admin_broadcast_done", sent=sent, total=total, failed=failed))


# ════════════════════════════════════════════════════════════
#  CALLBACK ROUTERI
# ════════════════════════════════════════════════════════════
def register(client: Client) -> None:

    @client.on_message(filters.command("admin") & filters.private)
    async def admin_cmd(c, m: Message):
        uid = m.from_user.id
        lang = await _lang(uid)
        if not handlers.is_admin(uid):
            await m.reply(t(lang, "admin_only"))
            return
        await show_panel(c, m.chat.id, lang)

    @client.on_callback_query(filters.regex(r"^adm:"))
    async def admin_cb(c, call: CallbackQuery):
        uid = call.from_user.id
        lang = await _lang(uid)
        if not handlers.is_admin(uid):
            await call.answer(t(lang, "admin_only"), show_alert=True)
            return
        await call.answer()
        data = call.data
        chat_id, msg_id = call.message.chat.id, call.message.id

        if data == "adm:panel":
            await show_panel(c, chat_id, lang, edit_message_id=msg_id)

        elif data == "adm:stats":
            await _show_stats(c, chat_id, lang, msg_id)

        elif data == "adm:weekly":
            await _show_weekly(c, chat_id, lang, msg_id)

        elif data == "adm:top_creators":
            await _show_top_creators(c, chat_id, lang, msg_id)

        elif data == "adm:top_readers":
            await _show_top_readers(c, chat_id, lang, msg_id)

        elif data == "adm:users":
            await _ask_search(c, chat_id, lang, uid, msg_id)

        elif data == "adm:limits":
            await _show_limits(c, chat_id, lang, msg_id)

        elif data.startswith("adm:glimit:"):
            field_map = {
                "create": "default_create_limit", "read": "default_read_limit",
                "max_images": "max_images_per_pdf",
            }
            kind = data.split(":")[2]
            handlers.admin_flow[uid] = {"action": "set_global_limit", "field": field_map[kind]}
            await c.edit_message_text(chat_id, msg_id, t(lang, "admin_ask_global_limit"))

        elif data == "adm:channels":
            await _show_channels(c, chat_id, lang, msg_id)

        elif data == "adm:chan_add":
            handlers.admin_flow[uid] = {"action": "add_channel"}
            await c.edit_message_text(chat_id, msg_id, t(lang, "admin_ask_channel"))

        elif data == "adm:chan_remove":
            handlers.admin_flow[uid] = {"action": "remove_channel"}
            await c.edit_message_text(chat_id, msg_id, t(lang, "admin_ask_remove_channel"))

        elif data == "adm:broadcast":
            handlers.admin_flow[uid] = {"action": "broadcast"}
            await c.edit_message_text(chat_id, msg_id, t(lang, "admin_broadcast_prompt"))

        elif data == "adm:db_check":
            ok = await database.ping_database()
            text = t(lang, "admin_db_ok" if ok else "admin_db_fail")
            await c.edit_message_text(chat_id, msg_id, text, reply_markup=keyboards.admin_back_kb(lang))

        elif data == "adm:memory":
            await _show_memory(c, chat_id, lang, msg_id)

        elif data == "adm:mem_clear_all":
            await _clear_all_memory(c, chat_id, lang)
            await _show_memory(c, chat_id, lang)

        elif data.startswith("adm:ban:"):
            target = int(data.split(":")[2])
            await database.set_banned(target, True)
            await _show_user_card(c, chat_id, lang, target, edit_msg_id=msg_id)

        elif data.startswith("adm:unban:"):
            target = int(data.split(":")[2])
            await database.set_banned(target, False)
            await _show_user_card(c, chat_id, lang, target, edit_msg_id=msg_id)

        elif data.startswith("adm:msg:"):
            target = int(data.split(":")[2])
            handlers.admin_reply_target[uid] = target
            handlers.admin_flow[uid] = {"action": "message_user", "target_uid": target}
            await c.edit_message_text(chat_id, msg_id, t(lang, "admin_ask_user_message"))

        elif data.startswith("adm:ulimit:"):
            _, _, kind, target_s = data.split(":")
            target = int(target_s)
            handlers.admin_flow[uid] = {"action": "set_user_limit", "kind": kind, "target_uid": target}
            await c.edit_message_text(chat_id, msg_id, t(lang, "admin_ask_limit_value"))
