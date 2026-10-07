# ════════════════════════════════════════════════════════════
#  ASOSIY (FOYDALANUVCHI) HANDLERLAR
#  init(app) main.py'dan chaqiriladi -- shundan keyin barcha
#  @app.on_message / @app.on_callback_query dekoratorlari ishlaydi.
# ════════════════════════════════════════════════════════════
import asyncio
import logging
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

# Har foydalanuvchi uchun lock -- ALBUM (media group) sifatida bir
# nechta rasm bir vaqtda (bir-biridan bir necha millisekund farq
# bilan) kelganda, ularni QATOR bilan (ketma-ket) qabul qilish uchun.
# Lock'siz bo'lsa: ikkinchi rasm, birinchisi hali `collect_state[uid]`
# ni yaratib ulgurmasdan kelib qolishi mumkin -- natijada ikkalasi
# "men birinchi rasmman" deb o'ylab, bir-birining holatini ezib
# tashlaydi (faqat 1 ta rasm qolib ketishi aynan shundan).
_photo_locks: dict[int, asyncio.Lock] = {}


# PDF o'qish (sahifalarga ajratish) jarayonida bo'lgan foydalanuvchilar
# -- shu orada yangi PDF yuborsa, "band" deb ogohlantiramiz.
_reading_pdf_users: set[int] = set()


def _get_photo_lock(uid: int) -> asyncio.Lock:
    lock = _photo_locks.get(uid)
    if lock is None:
        lock = asyncio.Lock()
        _photo_locks[uid] = lock
    return lock


# Bir foydalanuvchi bir vaqtda bir nechta PDF yuborsa (masalan albom
# sifatida 2-10 ta birga), ularning HAMMASI deyarli bir vaqtda kelib
# tushadi -- shuning uchun "band" tekshiruvi ham lock bilan
# himoyalanishi kerak (aks holda ikkinchisi ham "bo'sh" deb o'ylab
# qoladi va parallel ishga tushadi).
_document_locks: dict[int, asyncio.Lock] = {}


def _get_document_lock(uid: int) -> asyncio.Lock:
    lock = _document_locks.get(uid)
    if lock is None:
        lock = asyncio.Lock()
        _document_locks[uid] = lock
    return lock


async def _auto_delete_later(client: Client, chat_id: int, message_id: int, delay: float):
    """Ogohlantirish xabarlari ("band" degan xabarlar) chatda abadiy
    qolib ketmasligi uchun -- bir necha soniyadan keyin o'zi o'chadi."""
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, message_id)
    except Exception:
        pass


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
# A'zolik tekshiruvi natijasi (faqat "a'zo" natijasi) qisqa muddat
# eslab qolinadi -- har rasmda Telegram'ga so'rov yubormaslik uchun.
_member_cache: dict = {}
_MEMBER_TTL = 120  # soniya


async def _check_tg_membership(client: Client, uid: int, channel_id: str) -> bool:
    try:
        member = await client.get_chat_member(channel_id, uid)
        return member.status not in ("left", "kicked", "banned")
    except Exception:
        return False


async def check_force_sub(client: Client, uid: int, chat_id: int, send_message: bool = True) -> bool:
    """True -- bloklangan (xabar yuborilgan, davom etmang). False --
    bot ishlataveradi.

    MANTIQ: tashqi havolalar (Instagram va h.k.) HECH QACHON a'zolikni
    tekshirilmaydi va ALOHIDA/bir martalik ko'rsatilmaydi -- ular
    FAQAT foydalanuvchi hali MAJBURIY (Telegram) kanal(lar)ga obuna
    bo'lmagan paytda, shu kanallar ro'yxati bilan BIRGA (oxirida)
    ko'rsatiladi. Agar foydalanuvchi barcha majburiy kanallarga
    obuna bo'lgan bo'lsa -- tashqi havolalar UMUMAN ko'rsatilmaydi,
    chunki ular bloklovchi emas va o'z holicha hech qachon alohida
    talab qilinmaydi.

    `send_message=False` bo'lsa -- xabar YUBORILMAYDI, faqat natija
    qaytariladi (masalan "✅ Tekshirish" tugmasi bosilganda kerak)."""
    channels = await database.list_active_required_channels()
    if not channels:
        return False

    tg_channels = [ch for ch in channels if not str(ch["channel_id"]).startswith("ext:")]
    ext_channels = [ch for ch in channels if str(ch["channel_id"]).startswith("ext:")]

    tg_not_joined = []
    for ch in tg_channels:
        ref, _ = keyboards.channel_ref_and_url(str(ch["channel_id"]))
        if _member_cache.get((uid, ref), 0) > time.monotonic():
            continue  # yaqinda tekshirilgan -- a'zo
        if await _check_tg_membership(client, uid, ref):
            _member_cache[(uid, ref)] = time.monotonic() + _MEMBER_TTL
            await database.record_channel_join(ch["id"], uid)
        else:
            tg_not_joined.append(ch)

    if not tg_not_joined:
        return False  # barcha majburiy kanallarga obuna -- tashqi havolalar ko'rsatilmaydi

    not_joined = tg_not_joined + ext_channels
    if send_message:
        lang = await _lang(uid)
        try:
            await client.send_message(
                chat_id, t(lang, "force_sub_required"),
                reply_markup=keyboards.force_sub_kb(not_joined, lang),
            )
        except Exception:
            pass
    return True


# ════════════════════════════════════════════════════════════
#  RASM YIG'ISH / PDF YARATISH OQIMI
# ════════════════════════════════════════════════════════════
async def _cancel_collecting(uid: int, delete_status: bool = False, client: Client = None):
    _gate_cache.pop(uid, None)
    state = collect_state.pop(uid, None)
    if not state:
        return
    build_task = state.get("build_task")
    if build_task and not build_task.done():
        build_task.cancel()
    tmp_output_path = state.get("tmp_output_path")
    paths = await database.remove_pending_files_for_user(uid)
    all_paths = set(paths + state.get("images", []))
    if tmp_output_path:
        all_paths.add(tmp_output_path)
    for p in all_paths:
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


async def _collecting_status_text(lang: str, uid: int, count: int) -> str:
    max_images = await database.get_max_images_per_pdf(uid)
    max_suffix = t(lang, "collecting_status_max_suffix", max=max_images) if max_images > 0 else ""
    return t(lang, "collecting_status", count=count, max_suffix=max_suffix)


async def _update_status(client: Client, uid: int, lang: str):
    state = collect_state.get(uid)
    if not state:
        return
    text = await _collecting_status_text(lang, uid, len(state["images"]))
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
    PDF qurishni boshlab yuboradi. Foydalanuvchi "Tayyor" bosmasdan
    jim qolgan bo'lsa (ehtimol chatdan chiqib ketgan) -- ENDI nom
    so'rab yana 30 soniya KUTILMAYDI, standart nom bilan to'g'ridan
    to'g'ri PDF qurilib yuboriladi (xotirani bekorga ushlab turmaslik
    uchun)."""
    await asyncio.sleep(timeout)
    state = collect_state.get(uid)
    if not state or state.get("token") != token:
        return  # eskirgan watchdog -- holat allaqachon o'zgargan

    if state["stage"] == "collecting":
        await _start_build_and_ask_filename(client, uid, auto_timeout=True)
    elif state["stage"] == "awaiting_filename":
        await _finish_build(client, uid, filename=None)


def _bump_token(uid: int) -> int:
    state = collect_state[uid]
    state["token"] = state.get("token", 0) + 1
    return state["token"]


# ════════════════════════════════════════════════════════════
#  RASM QABUL QILISH -- "File-To-Zip" botining tez ishlash uslubida:
#   * limit/obuna tekshiruvi har rasmda EMAS, batch boshida BIR marta
#   * hisoblagichlar xotirada (bazaga har rasmda yozilmaydi)
#   * yuklab olish lock ICHIDA emas -- rasmlar parallel yuklanadi
#   * har rasmdan keyin xabar TAHRIRLANMAYDI: 1.5 soniyadan keyin bitta
#     "qabul qilinmoqda..." xabari, hammasi yuklangach -- BITTA yakuniy
#     xabar ("N ta rasm qabul qilindi" + Tayyor tugmasi)
# ════════════════════════════════════════════════════════════
_logger = logging.getLogger(__name__)

# uid -> {"downloading": int, "accepted": int, "rejected": int,
#         "recv_msg": Message | None, "timer": Task | None}
_recv: dict[int, dict] = {}
_RECV_NOTICE_DELAY = 1.5   # soniya
_GATE_TTL = 20             # soniya -- tekshiruv natijasi shuncha eslab qolinadi
_gate_cache: dict[int, tuple] = {}
_warn_ts: dict[int, float] = {}


def _get_recv(uid: int) -> dict:
    r = _recv.get(uid)
    if r is None:
        r = {"downloading": 0, "accepted": 0, "rejected": 0, "recv_msg": None, "timer": None}
        _recv[uid] = r
    return r


async def _safe_delete(message):
    if message is None:
        return
    try:
        await message.delete()
    except Exception:
        pass


def _remove_file(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


async def _warn_once(client: Client, chat_id: int, uid: int, text: str):
    """Ogohlantirishni har rasm uchun emas, ~5 soniyada BIR marta yuboradi."""
    now = time.monotonic()
    if now - _warn_ts.get(uid, 0) < 5:
        return
    _warn_ts[uid] = now
    try:
        warn = await client.send_message(chat_id, text)
    except Exception:
        return
    asyncio.create_task(_auto_delete_later(client, warn.chat.id, warn.id, 6))


async def _gate_compute(client: Client, uid: int, chat_id: int) -> dict:
    user = await database.get_user(uid)
    if user and user.get("is_banned"):
        return {"ok": False}
    if await check_force_sub(client, uid, chat_id):
        return {"ok": False}
    lang = (user or {}).get("language") or config.DEFAULT_LANGUAGE

    # PDF YARATISH limiti tugagan bo'lsa -- rasm qabul qilinmaydi
    # (chatda qoldiriladi).
    allowed, used, limit = await database.can_create_pdf(uid)
    if not allowed:
        try:
            await client.send_message(
                chat_id,
                t(lang, "limit_create_reached", used=used, limit=limit) + "\n\n" + t(lang, "premium_ad"),
                reply_markup=keyboards.premium_ad_kb(lang),
            )
        except Exception:
            pass
        return {"ok": False}

    max_images = await database.get_max_images_per_pdf(uid)
    return {"ok": True, "lang": lang, "max_images": max_images}


async def _gate(client: Client, uid: int, chat_id: int) -> dict:
    """Bir vaqtda kelgan albom rasmlari BITTA tekshiruvni baham ko'radi;
    ijobiy natija _GATE_TTL soniya eslab qolinadi, salbiy -- yo'q."""
    now = time.monotonic()
    ent = _gate_cache.get(uid)
    if ent and ent[0] > now:
        return await asyncio.shield(ent[1])
    fut = asyncio.ensure_future(_gate_compute(client, uid, chat_id))
    _gate_cache[uid] = (now + _GATE_TTL, fut)
    try:
        res = await asyncio.shield(fut)
    except Exception:
        cur = _gate_cache.get(uid)
        if cur and cur[1] is fut:
            _gate_cache.pop(uid, None)
        raise
    if not res.get("ok"):
        cur = _gate_cache.get(uid)
        if cur and cur[1] is fut:
            _gate_cache.pop(uid, None)
    return res


async def _recv_notice_job(client: Client, uid: int, chat_id: int, lang: str):
    await asyncio.sleep(_RECV_NOTICE_DELAY)
    recv = _recv.get(uid)
    if not recv or recv["downloading"] <= 0 or recv["recv_msg"] is not None:
        return
    try:
        msg = await client.send_message(chat_id, t(lang, "receiving_images"))
    except Exception:
        return
    recv = _recv.get(uid)
    if recv and recv["downloading"] > 0:
        recv["recv_msg"] = msg
    else:
        await _safe_delete(msg)  # shu orada hammasi yuklanib bo'lgan


def _schedule_recv_notice(client: Client, uid: int, chat_id: int, lang: str):
    recv = _get_recv(uid)
    old = recv.get("timer")
    if old and not old.done():
        old.cancel()
    recv["timer"] = asyncio.ensure_future(_recv_notice_job(client, uid, chat_id, lang))


async def _delete_status_msg(client: Client, chat_id: int, msg_id: int):
    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass


async def _check_recv_complete(client: Client, uid: int, state: dict, lang: str, max_images: int):
    """Har rasm yuklanib bo'lgach chaqiriladi. Hali yuklanayotgan rasm
    bo'lsa -- hech narsa qilmaydi. Oxirgisi tugagach -- "qabul
    qilinmoqda" xabarini o'chirib, BITTA yakuniy xabar yuboradi."""
    recv = _recv.get(uid)
    if not recv or recv["downloading"] > 0:
        return

    # ── batch tugadi (quyidagi qism await'siz -- bo'linmaydi) ──
    timer = recv.get("timer")
    if timer and not timer.done():
        timer.cancel()
    recv["timer"] = None
    recv_msg = recv["recv_msg"]
    recv["recv_msg"] = None
    accepted = recv["accepted"]
    rejected = recv["rejected"]
    recv["accepted"] = 0
    recv["rejected"] = 0
    if recv_msg is not None:
        asyncio.create_task(_safe_delete(recv_msg))

    if collect_state.get(uid) is not state:
        return  # jarayon bekor qilingan yoki almashgan
    if not state["items"]:
        collect_state.pop(uid, None)  # birorta ham rasm yuklanmadi
        return
    if accepted == 0 and rejected == 0:
        return  # yangi hech narsa yo'q -- eski status xabari qoladi

    count = len(state["items"])
    max_suffix = t(lang, "collecting_status_max_suffix", max=max_images) if max_images > 0 else ""
    text = t(lang, "collecting_status", count=count, max_suffix=max_suffix)
    old_chat, old_id = state["status_chat_id"], state.get("status_msg_id")
    try:
        sent = await client.send_message(old_chat, text, reply_markup=keyboards.collecting_kb(lang))
    except Exception:
        return
    if collect_state.get(uid) is not state:
        await _safe_delete(sent)  # shu orada bekor qilingan
        return
    state["status_chat_id"] = sent.chat.id
    state["status_msg_id"] = sent.id
    if old_id:
        asyncio.create_task(_delete_status_msg(client, old_chat, old_id))

    if rejected > 0 and max_images > 0:
        asyncio.create_task(_warn_once(client, sent.chat.id, uid, t(lang, "max_images_reached", max=max_images)))

    token = _bump_token(uid)
    asyncio.create_task(_idle_watchdog(client, uid, token, config.COLLECT_IDLE_TIMEOUT))


async def _handle_photo(client: Client, message: Message):
    uid = message.from_user.id
    chat_id = message.chat.id

    gate = await _gate(client, uid, chat_id)
    if not gate.get("ok"):
        return
    lang = gate["lang"]
    max_images = gate["max_images"]

    # ── QABUL QILISH QARORI: pastdagi qism await'siz, shuning uchun
    #    albomning 10 ta rasmi bir-biriga xalaqit bermaydi ──
    state = collect_state.get(uid)
    if state and state["stage"] in ("awaiting_filename", "building"):
        key = "busy_building" if state["stage"] == "building" else "busy_awaiting_filename"
        asyncio.create_task(_safe_delete(message))
        asyncio.create_task(_warn_once(client, chat_id, uid, t(lang, key)))
        return

    recv = _get_recv(uid)
    done_count = len(state["items"]) if state else 0
    if max_images > 0 and done_count + recv["downloading"] >= max_images:
        recv["rejected"] += 1
        asyncio.create_task(_safe_delete(message))
        if recv["downloading"] == 0:
            # Hozir yuklanayotgan batch yo'q -- yakuniy xabar chiqmaydi,
            # shuning uchun to'g'ridan-to'g'ri ogohlantiramiz.
            recv["rejected"] = 0
            asyncio.create_task(_warn_once(client, chat_id, uid, t(lang, "max_images_reached", max=max_images)))
        return

    if state is None:
        state = {
            "images": [], "items": {},
            "status_chat_id": chat_id, "status_msg_id": None,
            "stage": "collecting", "token": 0,
        }
        collect_state[uid] = state
    state.setdefault("items", {})
    _bump_token(uid)  # eski idle-watchdog eskirdi
    recv["downloading"] += 1
    if recv["downloading"] == 1:
        _schedule_recv_notice(client, uid, chat_id, lang)

    # ── YUKLAB OLISH (lock'siz -- parallel) ──
    local_path = os.path.join(_user_temp_dir(uid), f"img_{message.id}_{int(time.time() * 1000)}.jpg")
    ok = False
    try:
        await message.download(file_name=local_path)
        ok = True
    except Exception as e:
        _logger.warning("Rasm yuklab olinmadi (uid=%s): %s", uid, e)
    finally:
        recv["downloading"] = max(0, recv["downloading"] - 1)

    if ok and collect_state.get(uid) is state and state["stage"] == "collecting":
        state["items"][message.id] = local_path
        # Sahifalar tartibi -- yuborilgan tartibda (xabar ID bo'yicha),
        # parallel yuklash tugash tartibiga bog'liq bo'lmasin.
        state["images"] = [state["items"][k] for k in sorted(state["items"])]
        recv["accepted"] += 1
        size = pdf_utils.get_file_size(local_path)
        database.run_in_background(database.record_image_sent, uid, size)
        database.run_in_background(database.add_pending_file, uid, local_path, size)
        asyncio.create_task(_safe_delete(message))  # rasm chatdan tozalanadi
    else:
        _remove_file(local_path)  # xato yoki jarayon bekor qilingan

    await _check_recv_complete(client, uid, state, lang, max_images)


async def _collect_done_cb(client: Client, call: CallbackQuery):
    uid = call.from_user.id
    state = collect_state.get(uid)
    await call.answer()
    if not state or state["stage"] != "collecting" or not state["images"]:
        return
    if _recv.get(uid, {}).get("downloading", 0) > 0:
        return  # yangi rasmlar hali yuklanmoqda -- tugagach yangi "Tayyor" chiqadi
    await _start_build_and_ask_filename(client, uid, auto_timeout=False)


async def _start_build_and_ask_filename(client: Client, uid: int, auto_timeout: bool):
    """"Tayyor" bosilgach (yoki rasm yuborish 30 soniya to'xtab qolgach)
    chaqiriladi. G'OYA: PDF qurishni DARHOL fonda boshlaymiz (vaqtinchalik
    nom bilan) -- foydalanuvchi nom yozib ulgurguncha, PDF allaqachon
    tayyor bo'lib turadi, shuning uchun nom kiritilgach DARHOL (qurish
    kutilmasdan) yuboriladi -- juda tez tuyuladi.

    `auto_timeout=True` bo'lsa (foydalanuvchi "Tayyor" bosmay, 30 soniya
    rasm yubormay jim qolgan) -- ENDI nom so'ramaymiz, standart nom bilan
    to'g'ridan to'g'ri yakunlaymiz (ortiqcha 30 soniya xotirani band
    qilib turmaslik uchun)."""
    state = collect_state.get(uid)
    if not state:
        return
    lang = await _lang(uid)

    # Stage'ni DARHOL "building"ga o'tkazamiz -- shu lahzadan boshlab
    # `_handle_photo` yangi kelgan rasmlarni "jarayon band" deb bilib,
    # qabul qilmaydi (aks holda ular qurilish uchun olingan ro'yxatga
    # kirmay, orphan fayl bo'lib qolib ketishi mumkin edi).
    state["stage"] = "building"

    try:
        await client.edit_message_text(state["status_chat_id"], state["status_msg_id"], t(lang, "pdf_building"))
    except Exception:
        pass

    # PDF qurishni FONDA, HOZIROQ boshlaymiz -- vaqtinchalik (noyob)
    # nom bilan. Natija tugagach, `state["build_result"]` ga yoziladi.
    temp_dir = _user_temp_dir(uid)
    tmp_output_path = os.path.join(temp_dir, f"tmp_{uid}_{int(time.time() * 1000)}.pdf")
    state["build_task"] = asyncio.create_task(
        _run_pdf_build(list(state["images"]), tmp_output_path)
    )
    state["tmp_output_path"] = tmp_output_path

    if auto_timeout:
        # Foydalanuvchi allaqachon jim -- nom so'rab yana kutmaymiz,
        # darhol standart nom bilan yakunlaymiz.
        await _finish_build(client, uid, filename=None)
        return

    state["stage"] = "awaiting_filename"
    try:
        await client.edit_message_text(
            state["status_chat_id"], state["status_msg_id"], t(lang, "ask_filename"),
        )
    except Exception:
        pass
    token = _bump_token(uid)
    asyncio.create_task(_idle_watchdog(client, uid, token, config.FILENAME_WAIT_TIMEOUT))


async def _run_pdf_build(images: list[str], output_path: str) -> Exception | None:
    """Fon vazifasi -- rasmlarni PDF'ga aylantiradi. Xato bo'lsa, uni
    qaytaradi (chaqiruvchi tomonda tekshiriladi), aks holda None."""
    try:
        await asyncio.to_thread(pdf_utils.images_to_pdf, images, output_path)
        return None
    except Exception as e:
        return e


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
    await _finish_build(client, uid, filename=filename or None)


async def _finish_build(client: Client, uid: int, filename: str | None):
    """Nom kelgach (yoki nom kutish vaqti tugagach) chaqiriladi. PDF
    qurish fon vazifasi HALI TUGAMAGAN bo'lsa -- shu yerda tugashini
    kutamiz (odatda foydalanuvchi yozib ulgurgan vaqtda allaqachon
    tayyor bo'ladi, shuning uchun amalda kutish deyarli bo'lmaydi);
    tugagan bo'lsa -- faylni darhol kerakli nomga o'tkazib yuboramiz."""
    _gate_cache.pop(uid, None)  # PDF yaratilgach limit qayta tekshirilsin
    state = collect_state.pop(uid, None)
    if not state:
        return
    lang = await _lang(uid)

    build_task = state.get("build_task")
    tmp_output_path = state.get("tmp_output_path")
    if build_task is None or tmp_output_path is None:
        # Ehtiyot chorasi -- odatda bo'lmaydi, lekin build boshlanmagan
        # bo'lsa ham PDF qurib yuboramiz.
        tmp_output_path = os.path.join(_user_temp_dir(uid), f"tmp_{uid}_{int(time.time() * 1000)}.pdf")
        build_error = await _run_pdf_build(state["images"], tmp_output_path)
    else:
        build_error = await build_task

    if build_error is not None:
        try:
            await client.send_message(state["status_chat_id"], t(lang, "pdf_read_error"))
        except Exception:
            pass
        await _cleanup_build(uid, state, tmp_output_path)
        return

    if not filename:
        user = await database.get_user(uid)
        filename = (user or {}).get("first_name") or "document"
    filename = "".join(c for c in filename if c not in '\\/:*?"<>|').strip() or "document"

    temp_dir = _user_temp_dir(uid)
    final_path = os.path.join(temp_dir, f"{filename}.pdf")
    try:
        if tmp_output_path != final_path:
            os.replace(tmp_output_path, final_path)
    except Exception:
        final_path = tmp_output_path  # nom almashtirib bo'lmasa, baribir yuboramiz

    size = pdf_utils.get_file_size(final_path)
    await database.record_pdf_created(uid, size)

    try:
        await client.delete_messages(state["status_chat_id"], state["status_msg_id"])
    except Exception:
        pass

    bot_username = config.BOT_USERNAME or _bot_username_cached
    await client.send_document(
        state["status_chat_id"], final_path,
        caption=t(lang, "pdf_ready_caption", filename=f"{filename}.pdf", bot_username=bot_username),
    )

    await _send_limit_status_and_maybe_ad(client, state["status_chat_id"], uid, lang, kind="create")
    await _cleanup_build(uid, state, final_path)


async def _send_limit_status_and_maybe_ad(client: Client, chat_id: int, uid: int, lang: str, kind: str):
    """PDF yasalgan/o'qilgandan KEYIN chaqiriladi -- kichik xabarda
    bugungi limitdan qanchasi ishlatilgani va qanchasi qolganini
    ko'rsatadi. Limit TUGAGAN bo'lsa (qolgan=0, limit cheklovsiz
    emas) -- o'rniga premium reklamasi (hozircha test) chiqadi."""
    if kind == "create":
        _, used, limit = await database.can_create_pdf(uid)
        status_key, zero_left_show_ad = "limit_status_after_create", True
    else:
        _, used, limit = await database.can_read_pdf(uid)
        status_key, zero_left_show_ad = "limit_status_after_read", True

    if limit <= 0:
        return  # cheklovsiz -- hech narsa ko'rsatmaymiz

    left = max(0, limit - used)
    if left == 0 and zero_left_show_ad:
        try:
            await client.send_message(chat_id, t(lang, "premium_ad"), reply_markup=keyboards.premium_ad_kb(lang))
        except Exception:
            pass
    else:
        try:
            await client.send_message(chat_id, t(lang, status_key, used=used, limit=limit, left=left))
        except Exception:
            pass


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
        # Rasm yig'ish/nom kutish/PDF qurish jarayoni band -- "bir ish
        # payti boshqasi bajarilmaydi" -- foydalanuvchiga buni aytamiz.
        stage = collect_state[uid]["stage"]
        if stage == "building":
            key = "busy_building"
        else:
            # "collecting" yoki "awaiting_filename" -- ikkalasida ham
            # foydalanuvchi hali rasm to'plamini yakunlamagan, shuning
            # uchun bir xil "avval tugating" ogohlantirishi yetarli.
            key = "busy_awaiting_filename"
        try:
            await message.delete()
        except Exception:
            pass
        warn = await client.send_message(message.chat.id, t(lang, key))
        asyncio.create_task(_auto_delete_later(client, warn.chat.id, warn.id, 6))
        return

    async with _get_document_lock(uid):
        if uid in _reading_pdf_users:
            # Bir vaqtda (masalan albom sifatida) bir nechtasi kelgan --
            # faqat BIRINCHISI qabul qilinadi, qolganlari rad etiladi va
            # CHATDA QOLDIRILADI (foydalanuvchi ularni birma-bir, avvalgi
            # PDF o'qilib bo'lgach, qayta yuborishi kerak).
            warn = await message.reply(t(lang, "busy_reading_pdf"))
            asyncio.create_task(_auto_delete_later(client, warn.chat.id, warn.id, 8))
            return

        allowed, used, limit = await database.can_read_pdf(uid)
        if not allowed:
            await message.reply(
                t(lang, "limit_read_reached", used=used, limit=limit) + "\n\n" + t(lang, "premium_ad"),
                reply_markup=keyboards.premium_ad_kb(lang),
            )
            return

        _reading_pdf_users.add(uid)

    try:
        await _process_incoming_pdf(client, message, uid, lang)
    finally:
        _reading_pdf_users.discard(uid)


async def _process_incoming_pdf(client: Client, message: Message, uid: int, lang: str):
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

    await _send_limit_status_and_maybe_ad(client, message.chat.id, uid, lang, kind="read")
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

    await message.reply(t(lang, "message_sent_to_admin"), reply_markup=keyboards.main_menu_kb(lang, is_admin(uid)))


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
            reply_markup=keyboards.main_menu_kb(lang, is_admin(uid)),
        )

    @client.on_callback_query(filters.regex(r"^lang:(uz|en)$"))
    async def lang_cb(c, call: CallbackQuery):
        lang = call.data.split(":")[1]
        uid = call.from_user.id
        existing_user = await database.get_user(uid)
        already_had_lang = bool(existing_user and existing_user.get("language"))
        await database.set_user_language(uid, lang)
        await call.answer()
        try:
            await call.message.delete()
        except Exception:
            pass
        if already_had_lang:
            # "Til" tugmasi orqali o'zgartirilgan -- xush kelibsiz
            # xabarini qaytadan ko'rsatmaymiz, shunchaki tasdiqlaymiz.
            await c.send_message(
                call.message.chat.id, t(lang, "language_set"),
                reply_markup=keyboards.main_menu_kb(lang, is_admin(uid)),
            )
            return
        if await check_force_sub(c, uid, call.message.chat.id):
            return
        await c.send_message(
            call.message.chat.id, t(lang, "start_text", name=call.from_user.first_name or ""),
            reply_markup=keyboards.main_menu_kb(lang, is_admin(uid)),
        )

    @client.on_callback_query(filters.regex(r"^force_sub:noop$"))
    async def force_sub_noop_cb(c, call: CallbackQuery):
        await call.answer()

    @client.on_callback_query(filters.regex(r"^premium:info$"))
    async def premium_info_cb(c, call: CallbackQuery):
        # Premium hali ishlab chiqilmagan -- test (qisqa) popup xabari.
        lang = await _lang(call.from_user.id)
        await call.answer(t(lang, "premium_info_popup"), show_alert=True)

    @client.on_callback_query(filters.regex(r"^force_sub:check$"))
    async def force_sub_check_cb(c, call: CallbackQuery):
        uid = call.from_user.id
        lang = await _lang(uid)
        # send_message=False -- chatda ALLAQACHON turgan "obuna
        # bo'ling" xabari yetarli, yana bittasini yubormaymiz.
        still_blocked = await check_force_sub(c, uid, call.message.chat.id, send_message=False)
        if still_blocked:
            await call.answer(t(lang, "force_sub_still_not"), show_alert=True)
            return
        await call.answer()
        try:
            await call.message.delete()
        except Exception:
            pass
        await c.send_message(call.message.chat.id, t(lang, "force_sub_passed"), reply_markup=keyboards.main_menu_kb(lang, is_admin(uid)))

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
        lambda _, __, m: m.text in (t("uz", "menu_btn_profile"), t("en", "menu_btn_profile"))
    ))
    async def menu_profile(c, m):
        uid = m.from_user.id
        lang = await _lang(uid)
        user = await database.get_user(uid)
        if not user:
            return
        _, used_create, limit_create = await database.can_create_pdf(uid)
        _, used_read, limit_read = await database.can_read_pdf(uid)
        max_images = await database.get_max_images_per_pdf(uid)
        limit_create_s = str(limit_create) if limit_create > 0 else t(lang, "profile_unlimited")
        limit_read_s = str(limit_read) if limit_read > 0 else t(lang, "profile_unlimited")
        max_images_s = str(max_images) if max_images > 0 else t(lang, "profile_unlimited")
        # Premium hali ishlab chiqilmagan -- hozircha hamma "Oddiy" daraja.
        tier = t(lang, "profile_tier_standard")
        await m.reply(t(
            lang, "profile_text",
            id=uid, name=m.from_user.first_name or "", tier=tier,
            used_create=used_create, limit_create=limit_create_s,
            used_read=used_read, limit_read=limit_read_s,
            max_images=max_images_s,
            images_sent=user.get("images_sent") or 0,
            pdfs_created=user.get("pdfs_created") or 0,
            pdfs_read=user.get("pdfs_read") or 0,
        ))

    @client.on_message(filters.private & filters.text & filters.create(
        lambda _, __, m: m.text in (t("uz", "menu_btn_stats"), t("en", "menu_btn_stats"))
    ))
    async def menu_stats(c, m):
        # Umumiy bot statistikasi -- FAQAT jami ko'rsatkichlar, bugungi
        # ma'lumotlar bu yerda ko'rsatilmaydi (ular faqat admin panelda).
        lang = await _lang(m.from_user.id)
        s = await database.get_overview_stats()
        await m.reply(t(
            lang, "public_stats_text",
            total_users=s["total_users"], total_images=s["total_images"],
            total_pdfs=s["total_pdfs"], total_read=s["total_read"],
        ))

    @client.on_message(filters.private & filters.text & filters.create(
        lambda _, __, m: m.text in (t("uz", "menu_btn_language"), t("en", "menu_btn_language"))
    ))
    async def menu_language(c, m):
        lang = await _lang(m.from_user.id)
        await m.reply(t(lang, "choose_language"), reply_markup=keyboards.language_kb())

    @client.on_message(filters.private & filters.text & filters.create(
        lambda _, __, m: m.text in (t("uz", "menu_btn_premium"), t("en", "menu_btn_premium"))
    ))
    async def menu_premium(c, m):
        lang = await _lang(m.from_user.id)
        await m.reply(t(lang, "premium_ad"), reply_markup=keyboards.premium_ad_kb(lang))

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
            await m.reply(t(lang, "pdf_cancelled"), reply_markup=keyboards.main_menu_kb(lang, is_admin(uid)))
        else:
            await m.reply(t(lang, "start_text", name=m.from_user.first_name or ""), reply_markup=keyboards.main_menu_kb(lang, is_admin(uid)))

    @client.on_message(filters.private & filters.text & filters.create(
        lambda _, __, m: m.text in (t("uz", "menu_btn_admin_panel"), t("en", "menu_btn_admin_panel"))
    ))
    async def menu_admin_panel(c, m):
        uid = m.from_user.id
        lang = await _lang(uid)
        if not is_admin(uid):
            return
        from admin_panel import show_panel
        await show_panel(c, m.chat.id, lang)

    @client.on_message(filters.private & filters.photo)
    async def photo_msg(c, m):
        uid = m.from_user.id
        # Admin broadcast/foydalanuvchiga xabar yozish jarayonida bo'lsa
        # -- bu rasm PDF yig'ish uchun EMAS, balki admin oqimiga tegishli.
        if is_admin(uid) and uid in admin_flow:
            from admin_panel import handle_admin_media
            if await handle_admin_media(c, m):
                return
        await _handle_photo(c, m)

    @client.on_message(filters.private & filters.document)
    async def document_msg(c, m):
        uid = m.from_user.id
        if is_admin(uid) and uid in admin_flow:
            from admin_panel import handle_admin_media
            if await handle_admin_media(c, m):
                return
        doc = m.document
        is_pdf = doc and (doc.mime_type == "application/pdf" or (doc.file_name or "").lower().endswith(".pdf"))
        if is_pdf:
            await _handle_document(c, m)
        else:
            handled = await _media_router(c, m)

    @client.on_message(filters.private & (filters.video | filters.animation) & ~filters.document)
    async def media_msg(c, m):
        uid = m.from_user.id
        if is_admin(uid) and uid in admin_flow:
            from admin_panel import handle_admin_media
            if await handle_admin_media(c, m):
                return
        await _media_router(c, m)

    @client.on_message(filters.private & filters.text & ~filters.command("start"))
    async def text_msg(c, m):
        await _text_router(c, m)
