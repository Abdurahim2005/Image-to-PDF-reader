# ════════════════════════════════════════════════════════════
#  ASOSIY (FOYDALANUVCHI) HANDLERLAR
#  init(app) main.py'dan chaqiriladi -- shundan keyin barcha
#  @app.on_message / @app.on_callback_query dekoratorlari ishlaydi.
# ════════════════════════════════════════════════════════════
import asyncio
import os
import shutil
import time

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.errors import RPCError

import config
import database
from i18n import t
import keyboards
import pdf_utils

app: Client | None = None


# ── HOLAT (in-memory, jarayon davomida) ─────────────────────
# collect_state[uid] = {
#   "images": [path, ...], "status_chat_id": int, "status_msg_id": int,
#   "stage": "collecting" | "awaiting_filename", "token": int,
# }
collect_state: dict[int, dict] = {}
# Har foydalanuvchi uchun "token" -- eski idle-timeout task hali
# ishlayotgan bo'lsa-yu, foydalanuvchi yangi rasm yuborsa, eski task
# "men eskirganman" deb bilib, hech narsa qilmay chiqib ketishi uchun.

contact_state: set[int] = set()        # adminga xabar yozish jarayonidagilar
admin_reply_target: dict[int, int] = {}  # admin_id -> javob yozilayotgan foydalanuvchi id
admin_flow: dict[int, dict] = {}       # admin_id -> {"action": ..., ...}


def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


async def _lang(uid: int) -> str:
    return await database.get_user_language(uid)


def _user_temp_dir(uid: int) -> str:
    path = os.path.join(config.TEMP_DIR, str(uid))
    os.makedirs(path, exist_ok=True)
    return path


def _admin_username() -> str:
    return os.environ.get("ADMIN_USERNAME", "").lstrip("@")


def init(client: Client) -> None:
    global app
    app = client
    _register_user_handlers(client)


# ════════════════════════════════════════════════════════════
#  MAJBURIY OBUNA
# ════════════════════════════════════════════════════════════
async def _check_tg_membership(client: Client, uid: int, channel_id: str) -> bool:
    try:
        member = await client.get_chat_member(channel_id, uid)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return False


async def check_force_sub(client: Client, uid: int, chat_id: int) -> bool:
    """True -- bloklangan (xabar yuborilgan, davom etmang). False --
    bot ishlataveradi. Tashqi havolalar hech qachon bloklamaydi, lekin
    bloklovchi kanal bor paytda ular ham ro'yxatda oxirida chiqadi;
    bloklovchi kanal yo'q bo'lsa, faqat BIRINCHI marta chiqadi."""
    channels = await database.list_active_required_channels()
    if not channels:
        return False

    tg_not_joined, ext_channels = [], []
    blocking = False
    ext_first_time = False

    for ch in channels:
        channel_id = str(ch["channel_id"])
        if channel_id.startswith("ext:"):
            ext_channels.append(ch)
            if not await database.has_joined_channel(ch["id"], uid):
                ext_first_time = True
            continue
        if await _check_tg_membership(client, uid, channel_id):
            await database.record_channel_join(ch["id"], uid)
        else:
            tg_not_joined.append(ch)
            blocking = True

    not_joined = list(tg_not_joined)
    if blocking:
        not_joined.extend(ext_channels)
    elif ext_first_time:
        not_joined.extend(
            ch for ch in ext_channels if not await database.has_joined_channel(ch["id"], uid)
        )

    if not_joined and (blocking or ext_first_time):
        for ch in ext_channels:
            await database.record_channel_join(ch["id"], uid)

    if blocking or ext_first_time:
        lang = await _lang(uid)
        try:
            await client.send_message(
                chat_id, t(lang, "force_sub_required"),
                reply_markup=keyboards.force_sub_kb(not_joined, lang),
            )
        except Exception:
            pass
        return blocking

    return False


# ════════════════════════════════════════════════════════════
#  RASM YIG'ISH / PDF YARATISH OQIMI
# ════════════════════════════════════════════════════════════
async def _cancel_collecting(uid: int, delete_status: bool = False, client: Client = None):
    state = collect_state.pop(uid, None)
    if not state:
        return
    paths = await database.remove_pending_files_for_user(uid)
    for p in set(paths + state.get("images", [])):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    if delete_status and client and state.get("status_msg_id"):
        try:
            await client.delete_messages(state["status_chat_id"], state["status_msg_id"])
        except Exception:
            pass


async def _update_status(client: Client, uid: int, lang: str):
    state = collect_state.get(uid)
    if not state:
        return
    text = t(lang, "collecting_status", count=len(state["images"]))
    try:
        await client.edit_message_text(
            state["status_chat_id"], state["status_msg_id"], text,
            reply_markup=keyboards.collecting_kb(lang),
        )
    except Exception:
        pass


async def _idle_watchdog(client: Client, uid: int, token: int, timeout: float):
    """`timeout` soniya kutadi; agar shu orada holat hali ham mavjud
    bo'lsa VA token hali eng oxirgisi bo'lsa (ya'ni foydalanuvchi shu
    orada yangi rasm yubormagan/tugma bosmagan bo'lsa) -- avtomatik
    keyingi bosqichga o'tkazadi."""
    await asyncio.sleep(timeout)
    state = collect_state.get(uid)
    if not state or state.get("token") != token:
        return  # eskirgan watchdog -- holat allaqachon o'zgargan

    if state["stage"] == "collecting":
        await _finalize_to_filename_prompt(client, uid)
    elif state["stage"] == "awaiting_filename":
        await _build_pdf(client, uid, filename=None)


def _bump_token(uid: int) -> int:
    state = collect_state[uid]
    state["token"] = state.get("token", 0) + 1
    return state["token"]


async def _handle_photo(client: Client, message: Message):
    uid = message.from_user.id
    lang = await _lang(uid)

    user = await database.get_user(uid)
    if user and user.get("is_banned"):
        return
    if await check_force_sub(client, uid, message.chat.id):
        return

    state = collect_state.get(uid)
    if state and state["stage"] == "awaiting_filename":
        # Nom kutilayotganda rasm yuborilsa -- e'tiborsiz qoldiramiz
        # ("bir ish payti boshqasi bajarilmaydi").
        return

    temp_dir = _user_temp_dir(uid)
    local_path = os.path.join(temp_dir, f"img_{int(time.time() * 1000)}.jpg")
    await message.download(file_name=local_path)
    size = pdf_utils.get_file_size(local_path)
    await database.record_image_sent(uid, size)
    await database.add_pending_file(uid, local_path, size)

    # Rasm chatdan DOIM tozalanadi (qabul bo'lgach)
    try:
        await message.delete()
    except Exception:
        pass

    if not state:
        status_msg = await client.send_message(
            message.chat.id, t(lang, "collecting_status", count=1),
            reply_markup=keyboards.collecting_kb(lang),
        )
        collect_state[uid] = {
            "images": [local_path],
            "status_chat_id": message.chat.id,
            "status_msg_id": status_msg.id,
            "stage": "collecting",
            "token": 0,
        }
    else:
        state["images"].append(local_path)
        await _update_status(client, uid, lang)

    token = _bump_token(uid)
    asyncio.create_task(_idle_watchdog(client, uid, token, config.COLLECT_IDLE_TIMEOUT))


async def _collect_done_cb(client: Client, call: CallbackQuery):
    uid = call.from_user.id
    lang = await _lang(uid)
    state = collect_state.get(uid)
    await call.answer()
    if not state or state["stage"] != "collecting" or not state["images"]:
        return
    await _finalize_to_filename_prompt(client, uid)


async def _finalize_to_filename_prompt(client: Client, uid: int):
    state = collect_state.get(uid)
    if not state:
        return
    lang = await _lang(uid)
    state["stage"] = "awaiting_filename"
    try:
        await client.edit_message_text(
            state["status_chat_id"], state["status_msg_id"], t(lang, "ask_filename"),
        )
    except Exception:
        pass
    token = _bump_token(uid)
    asyncio.create_task(_idle_watchdog(client, uid, token, config.FILENAME_WAIT_TIMEOUT))


async def _collect_cancel_cb(client: Client, call: CallbackQuery):
    uid = call.from_user.id
    lang = await _lang(uid)
    await call.answer()
    await _cancel_collecting(uid, delete_status=True, client=client)
    await client.send_message(call.message.chat.id, t(lang, "pdf_cancelled"))


async def _handle_filename_text(client: Client, message: Message, state: dict):
    uid = message.from_user.id
    filename = (message.text or "").strip()[:100]
    try:
        await message.delete()
    except Exception:
        pass
    await _build_pdf(client, uid, filename=filename or None)


async def _build_pdf(client: Client, uid: int, filename: str | None):
    state = collect_state.pop(uid, None)
    if not state:
        return
    lang = await _lang(uid)

    try:
        await client.edit_message_text(state["status_chat_id"], state["status_msg_id"], t(lang, "pdf_building"))
    except Exception:
        pass

    if not filename:
        user = await database.get_user(uid)
        filename = (user or {}).get("first_name") or "document"
    filename = "".join(c for c in filename if c not in '\\/:*?"<>|').strip() or "document"

    temp_dir = _user_temp_dir(uid)
    output_path = os.path.join(temp_dir, f"{filename}.pdf")
    try:
        await asyncio.to_thread(pdf_utils.images_to_pdf, state["images"], output_path)
    except Exception:
        await client.send_message(state["status_chat_id"], t(lang, "pdf_read_error"))
        await _cleanup_build(uid, state, output_path)
        return

    size = pdf_utils.get_file_size(output_path)
    await database.record_pdf_created(uid, size)

    try:
        await client.delete_messages(state["status_chat_id"], state["status_msg_id"])
    except Exception:
        pass

    bot_username = config.BOT_USERNAME or _bot_username_cached
    await client.send_document(
        state["status_chat_id"], output_path,
        caption=t(lang, "pdf_ready_caption", filename=f"{filename}.pdf", bot_username=bot_username),
    )

    await _cleanup_build(uid, state, output_path)


_bot_username_cached = ""


async def _cleanup_build(uid: int, state: dict, output_path: str):
    await database.remove_pending_files_for_user(uid)
    for p in state.get("images", []):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    try:
        if os.path.exists(output_path):
            os.remove(output_path)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
#  PDF QABUL QILISH -> RASMLARGA AJRATISH
# ════════════════════════════════════════════════════════════
async def _handle_document(client: Client, message: Message):
    uid = message.from_user.id
    doc = message.document
    if not doc or (doc.mime_type != "application/pdf" and not (doc.file_name or "").lower().endswith(".pdf")):
        return

    lang = await _lang(uid)
    user = await database.get_user(uid)
    if user and user.get("is_banned"):
        return
    if await check_force_sub(client, uid, message.chat.id):
        return

    if uid in collect_state:
        # Rasm yig'ish/nom kutish jarayoni band -- "bir ish payti
        # boshqasi bajarilmaydi".
        return

    allowed, used, limit = await database.can_read_pdf(uid)
    if not allowed:
        await message.reply(t(lang, "limit_read_reached", used=used, limit=limit))
        return

    status = await message.reply(t(lang, "pdf_processing"))
    temp_dir = _user_temp_dir(uid)
    pdf_path = os.path.join(temp_dir, f"incoming_{int(time.time() * 1000)}.pdf")
    await message.download(file_name=pdf_path)
    pdf_size = pdf_utils.get_file_size(pdf_path)
    pending_id = await database.add_pending_file(uid, pdf_path, pdf_size)

    pages_dir = os.path.join(temp_dir, f"pages_{int(time.time() * 1000)}")
    try:
        page_paths = await asyncio.to_thread(pdf_utils.pdf_to_images, pdf_path, pages_dir)
    except Exception:
        try:
            await status.edit_text(t(lang, "pdf_read_error"))
        except Exception:
            pass
        await database.remove_pending_files_for_user(uid)
        _safe_remove_file(pdf_path)
        return

    if not page_paths:
        try:
            await status.edit_text(t(lang, "pdf_read_error"))
        except Exception:
            pass
        await database.remove_pending_files_for_user(uid)
        _safe_remove_file(pdf_path)
        shutil.rmtree(pages_dir, ignore_errors=True)
        return

    await database.record_pdf_read(uid, pdf_size)

    bot_username = config.BOT_USERNAME
    from pyrogram.types import InputMediaPhoto
    media_groups = [page_paths[i:i + 10] for i in range(0, len(page_paths), 10)]
    for gi, group in enumerate(media_groups):
        media = [InputMediaPhoto(p) for p in group]
        if gi == len(media_groups) - 1:
            media[-1].caption = t(lang, "pdf_done_caption", pages=len(page_paths), bot_username=bot_username)
        await client.send_media_group(message.chat.id, media)

    try:
        await status.delete()
    except Exception:
        pass

    await database.remove_pending_files_for_user(uid)
    _safe_remove_file(pdf_path)
    shutil.rmtree(pages_dir, ignore_errors=True)


def _safe_remove_file(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
#  ADMIN BILAN ALOQA (foydalanuvchi tomoni)
# ════════════════════════════════════════════════════════════
async def _forward_to_admin(client: Client, message: Message):
    uid = message.from_user.id
    contact_state.discard(uid)
    lang = await _lang(uid)
    user = await database.get_user(uid)
    name = message.from_user.first_name or ""
    username = message.from_user.username or "—"

    if not config.ADMIN_IDS:
        return
    admin_id = config.ADMIN_IDS[0]
    header = t(lang, "admin_new_message_prefix", name=name, username=username, id=uid)
    try:
        sent = await message.forward(admin_id)
        info_msg = await client.send_message(
            admin_id, header, reply_markup=keyboards.admin_reply_kb(lang, uid),
        )
        await database.link_admin_message(uid, sent.id)
        await database.link_admin_message(uid, info_msg.id)
    except Exception:
        pass

    await message.reply(t(lang, "message_sent_to_admin"), reply_markup=keyboards.main_menu_kb(lang))


# ════════════════════════════════════════════════════════════
#  TEXT FALLBACK -- nom kutish / admin xabari / admin javobi /
#  admin panel ketma-ket kiritishlar hammasi oddiy matn xabar
#  sifatida keladi, shuning uchun bitta joyda tartib bilan tekshiramiz.
# ════════════════════════════════════════════════════════════
async def _text_router(client: Client, message: Message):
    uid = message.from_user.id

    # 1) Admin o'z ketma-ket kiritish jarayonida bo'lsa (admin_panel.py
    #    ro'yxatdan o'tkazadi, bu yerda faqat chaqiramiz)
    if is_admin(uid):
        from admin_panel import handle_admin_text
        handled = await handle_admin_text(client, message)
        if handled:
            return

    # 2) Nom kutilayotgan bo'lsa
    state = collect_state.get(uid)
    if state and state["stage"] == "awaiting_filename":
        await _handle_filename_text(client, message, state)
        return

    # 3) Oddiy foydalanuvchi adminga xabar yozish jarayonida bo'lsa
    if uid in contact_state:
        await _forward_to_admin(client, message)
        return


async def _media_router(client: Client, message: Message):
    """Rasm/video/fayl -- agar foydalanuvchi adminga xabar yozish
    jarayonida bo'lsa, forward qilinadi (document bo'lsa va u PDF
    bo'lmasa ham shu yerga tushadi)."""
    uid = message.from_user.id
    if uid in contact_state:
        await _forward_to_admin(client, message)
        return True
    return False


# ════════════════════════════════════════════════════════════
#  HANDLER RO'YXATDAN O'TKAZISH
# ════════════════════════════════════════════════════════════
def _register_user_handlers(client: Client) -> None:

    @client.on_message(filters.command("start") & filters.private)
    async def start_cmd(c, m):
        uid = m.from_user.id
        is_new = await database.upsert_user(uid, m.from_user.username or "", m.from_user.first_name or "")
        user = await database.get_user(uid)
        if user and user.get("is_banned"):
            lang = await _lang(uid)
            await m.reply(t(lang, "banned"))
            return
        if is_new or not user or not user.get("language"):
            await m.reply(t("uz", "choose_language"), reply_markup=keyboards.language_kb())
            return
        lang = user.get("language") or config.DEFAULT_LANGUAGE
        if await check_force_sub(c, uid, m.chat.id):
            return
        await m.reply(
            t(lang, "start_text", name=m.from_user.first_name or ""),
            reply_markup=keyboards.main_menu_kb(lang),
        )

    @client.on_callback_query(filters.regex(r"^lang:(uz|en)$"))
    async def lang_cb(c, call: CallbackQuery):
        lang = call.data.split(":")[1]
        uid = call.from_user.id
        await database.set_user_language(uid, lang)
        await call.answer()
        try:
            await call.message.delete()
        except Exception:
            pass
        if await check_force_sub(c, uid, call.message.chat.id):
            return
        await c.send_message(
            call.message.chat.id, t(lang, "start_text", name=call.from_user.first_name or ""),
            reply_markup=keyboards.main_menu_kb(lang),
        )

    @client.on_callback_query(filters.regex(r"^force_sub:check$"))
    async def force_sub_check_cb(c, call: CallbackQuery):
        uid = call.from_user.id
        lang = await _lang(uid)
        still_blocked = await check_force_sub(c, uid, call.message.chat.id)
        if still_blocked:
            await call.answer(t(lang, "force_sub_still_not"), show_alert=True)
            return
        await call.answer()
        try:
            await call.message.delete()
        except Exception:
            pass
        await c.send_message(call.message.chat.id, t(lang, "force_sub_passed"), reply_markup=keyboards.main_menu_kb(lang))

    @client.on_callback_query(filters.regex(r"^collect:done$"))
    async def collect_done_cb(c, call: CallbackQuery):
        await _collect_done_cb(c, call)

    @client.on_callback_query(filters.regex(r"^collect:cancel$"))
    async def collect_cancel_cb(c, call: CallbackQuery):
        await _collect_cancel_cb(c, call)

    @client.on_message(filters.private & filters.text & filters.create(
        lambda _, __, m: m.text in (t("uz", "menu_btn_info"), t("en", "menu_btn_info"))
    ))
    async def menu_info(c, m):
        lang = await _lang(m.from_user.id)
        await m.reply(t(lang, "info_text", admin_username=_admin_username()))

    @client.on_message(filters.private & filters.text & filters.create(
        lambda _, __, m: m.text in (t("uz", "menu_btn_contact_admin"), t("en", "menu_btn_contact_admin"))
    ))
    async def menu_contact(c, m):
        uid = m.from_user.id
        lang = await _lang(uid)
        contact_state.add(uid)
        await m.reply(t(lang, "ask_message_for_admin"), reply_markup=keyboards.cancel_kb(lang))

    @client.on_message(filters.private & filters.text & filters.create(
        lambda _, __, m: m.text in (t("uz", "menu_btn_cancel"), t("en", "menu_btn_cancel"))
    ))
    async def menu_cancel(c, m):
        uid = m.from_user.id
        lang = await _lang(uid)
        contact_state.discard(uid)
        was_collecting = uid in collect_state
        await _cancel_collecting(uid, delete_status=True, client=c)
        if was_collecting:
            await m.reply(t(lang, "pdf_cancelled"), reply_markup=keyboards.main_menu_kb(lang))
        else:
            await m.reply(t(lang, "start_text", name=m.from_user.first_name or ""), reply_markup=keyboards.main_menu_kb(lang))

    @client.on_message(filters.private & filters.photo)
    async def photo_msg(c, m):
        await _handle_photo(c, m)

    @client.on_message(filters.private & filters.document)
    async def document_msg(c, m):
        doc = m.document
        is_pdf = doc and (doc.mime_type == "application/pdf" or (doc.file_name or "").lower().endswith(".pdf"))
        if is_pdf:
            await _handle_document(c, m)
        else:
            handled = await _media_router(c, m)

    @client.on_message(filters.private & (filters.video | filters.animation) & ~filters.document)
    async def media_msg(c, m):
        await _media_router(c, m)

    @client.on_message(filters.private & filters.text & ~filters.command("start"))
    async def text_msg(c, m):
        await _text_router(c, m)
