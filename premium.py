# ════════════════════════════════════════════════════════════
#  PREMIUM -- FOYDALANUVCHI TOMONI
#
#   * 🌟 Premium ekrani: tarif ma'lumoti (limitlar, narx, muddat) yoki,
#     agar premium faol bo'lsa, uning holati va tugash sanasi.
#   * To'lov oqimi:
#        bank karta (UZS)  yoki  kripto (USDT)  ->  rekvizitlar  ->
#        "To'ladim"  ->  chek (rasm yoki PDF)  ->  adminga tasdiqlashga
#     Admin tasdiqlasa premium avtomatik yoqiladi (admin_premium.py).
#   * Premium muddati tugaganda foydalanuvchiga xabar (fon-vazifa).
#
#  Narx, muddat va limitlar KOD ICHIDA EMAS -- admin paneldagi
#  "🌟 Premium" va "💳 To'lov sozlamalari" bo'limlaridan o'zgartiriladi
#  (database.get_settings()).
# ════════════════════════════════════════════════════════════
import asyncio
import logging
import time

from pyrogram import Client, ContinuePropagation, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

import config
import database
from i18n import t

logger = logging.getLogger(__name__)

# uid -> {"method", "method_detail", "amount", "currency", "plan_days",
#         "details_msg_id", "awaiting_receipt", "ask_msg_id", "ts"}
# To'lov jarayonidagi foydalanuvchining holati (xotirada; bot qayta
# ishga tushsa -- foydalanuvchi to'lovni boshidan boshlaydi).
payment_flow: dict[int, dict] = {}

# uid -> media_group_id: chek ALBOM (bir nechta rasm) bo'lib kelsa, birinchisi
# qabul qilinadi, qolganlari rasm->PDF jarayoniga tushib ketmasligi uchun
# jimgina yutiladi.
_receipt_groups: dict[int, str] = {}


# ════════════════════════════════════════════════════════════
#  FORMATLASH (admin_premium.py ham ishlatadi)
# ════════════════════════════════════════════════════════════
def fmt_uzs(value) -> str:
    """25000 -> '25 000'"""
    return f"{int(value):,}".replace(",", " ")


def fmt_usdt(value) -> str:
    """2.0 -> '2', 1.5 -> '1.5'"""
    return f"{float(value):g}"


def fmt_limit(lang: str, value) -> str:
    value = int(value or 0)
    return str(value) if value > 0 else t(lang, "profile_unlimited")


def fmt_duration(lang: str, days) -> str:
    days = int(days or 0)
    return t(lang, "premium_duration_days", days=days) if days > 0 else t(lang, "premium_duration_forever")


def fmt_until(lang: str, expires_at) -> str:
    """`expires_at` None/'' -- cheksiz premium."""
    if not expires_at:
        return t(lang, "premium_forever")
    return t(lang, "premium_until", date=str(expires_at)[:10])


def clean_name(text) -> str:
    """Foydalanuvchi ismini xabar formatlashiga xalaqit bermaydigan qilib
    tozalaydi (matn Markdown/HTML aralash rejimda yuboriladi)."""
    return "".join(ch for ch in (text or "") if ch not in "*`[]<>").strip()


def user_label(username, first_name, user_id) -> str:
    if username:
        return f"@{clean_name(username)}"
    return clean_name(first_name) or str(user_id)


def payment_caption(lang: str, pay: dict, label: str) -> str:
    amount = fmt_uzs(pay["amount"]) if pay["currency"] == "UZS" else pay["amount"]
    return t(
        lang, "ap_payment_caption",
        id=pay["id"], user=label, user_id=pay["user_id"], method=clean_name(pay["method_detail"]),
        amount=amount, currency=pay["currency"],
        duration=fmt_duration(lang, pay["plan_days"]), created=pay["created_at"],
    )


def payment_review_kb(lang: str, payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(t(lang, "ap_btn_approve"), callback_data=f"ap:payok:{payment_id}"),
        InlineKeyboardButton(t(lang, "ap_btn_reject"), callback_data=f"ap:payno:{payment_id}"),
    ]])


async def send_payment_review(client: Client, chat_id: int, lang: str, pay: dict, label: str | None = None):
    """To'lov so'rovini (chek + tugmalar) adminga yuboradi."""
    if label is None:
        row = await database.get_user(pay["user_id"]) or {}
        label = user_label(row.get("username"), row.get("first_name"), pay["user_id"])
    caption = payment_caption(lang, pay, label)
    kb = payment_review_kb(lang, pay["id"])
    if pay["receipt_type"] == "photo":
        await client.send_photo(chat_id, pay["receipt_file_id"], caption=caption, reply_markup=kb)
    else:
        await client.send_document(chat_id, pay["receipt_file_id"], caption=caption, reply_markup=kb)


async def notify_admins(client: Client, payment_id: int, from_user=None) -> None:
    pay = await database.get_payment(payment_id)
    if not pay:
        return
    label = user_label(
        getattr(from_user, "username", None), getattr(from_user, "first_name", None), pay["user_id"]
    )
    for admin_id in config.ADMIN_IDS:
        try:
            admin_lang = await database.get_user_language(admin_id)
            await send_payment_review(client, admin_id, admin_lang, pay, label)
        except Exception as e:
            logger.warning("To'lov so'rovini adminga (%s) yuborib bo'lmadi: %s", admin_id, e)


# ════════════════════════════════════════════════════════════
#  🌟 PREMIUM EKRANI
# ════════════════════════════════════════════════════════════
async def build_premium_screen(uid: int, lang: str) -> tuple[str, InlineKeyboardMarkup | None]:
    s = await database.get_settings()
    prem = await database.get_premium(uid)
    p_limits = dict(
        p_create=fmt_limit(lang, s["premium_create_limit"]),
        p_read=fmt_limit(lang, s["premium_read_limit"]),
        p_images=fmt_limit(lang, s["premium_max_images"]),
    )

    if prem:
        text = t(lang, "premium_active_text", until=fmt_until(lang, prem["expires_at"]), **p_limits)
        if prem["expires_at"] is None:
            return text, None  # cheksiz premium -- uzaytirishga hojat yo'q
        button = "premium_btn_extend"
    else:
        text = t(
            lang, "premium_info_text", **p_limits,
            s_create=fmt_limit(lang, s["default_create_limit"]),
            s_read=fmt_limit(lang, s["default_read_limit"]),
            s_images=fmt_limit(lang, s["max_images_per_pdf"]),
            duration=fmt_duration(lang, s["premium_days"]),
            price_uzs=fmt_uzs(s["price_uzs"]), price_usdt=fmt_usdt(s["price_usdt"]),
        )
        button = "premium_btn_buy"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, button), callback_data="pay:buy")]])
    return text, kb


async def show_premium(client: Client, chat_id: int, uid: int, lang: str) -> None:
    """handlers.py: '🌟 Premium' menyu tugmasi va 'Premium olish' inline
    tugmasi shu funksiyani chaqiradi."""
    text, kb = await build_premium_screen(uid, lang)
    await client.send_message(chat_id, text, reply_markup=kb)


# ════════════════════════════════════════════════════════════
#  TO'LOV OQIMI
# ════════════════════════════════════════════════════════════
async def _lang(uid: int) -> str:
    return await database.get_user_language(uid)


def _cancel_row(lang: str) -> list:
    return [InlineKeyboardButton(t(lang, "pay_btn_cancel"), callback_data="pay:cancel")]


async def _edit(client: Client, call: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None):
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except MessageNotModified:
        pass
    except Exception:
        await client.send_message(call.message.chat.id, text, reply_markup=kb)


async def _safe_delete(client: Client, chat_id: int, message_id) -> None:
    if not message_id:
        return
    try:
        await client.delete_messages(chat_id, message_id)
    except Exception:
        pass


def _paid_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "pay_btn_paid"), callback_data="pay:paid")],
        _cancel_row(lang),
    ])


async def _start_buy(client: Client, call: CallbackQuery):
    uid = call.from_user.id
    lang = await _lang(uid)
    await call.answer()
    cards, tokens = await database.list_cards(), await database.list_tokens()
    s = await database.get_settings()

    rows = []
    if cards:
        rows.append([InlineKeyboardButton(
            t(lang, "pay_btn_card", price=fmt_uzs(s["price_uzs"])), callback_data="pay:m:card")])
    if tokens:
        rows.append([InlineKeyboardButton(
            t(lang, "pay_btn_crypto", price=fmt_usdt(s["price_usdt"])), callback_data="pay:m:crypto")])
    if not rows:
        await client.send_message(call.message.chat.id, t(lang, "pay_unavailable"))
        return
    rows.append(_cancel_row(lang))

    payment_flow.pop(uid, None)
    await client.send_message(
        call.message.chat.id, t(lang, "pay_choose_method"), reply_markup=InlineKeyboardMarkup(rows)
    )


# ── Bank karta (UZS) ────────────────────────────────────────
async def _method_card(client: Client, call: CallbackQuery):
    lang = await _lang(call.from_user.id)
    await call.answer()
    cards = await database.list_cards()
    if not cards:
        await _edit(client, call, t(lang, "pay_no_cards"))
        return
    if len(cards) == 1:  # tanlash shart emas
        await _show_card(client, call, cards[0], lang)
        return
    buttons = [[InlineKeyboardButton(
        f"{cd['bank_name']} •• {cd['card_number'][-4:]}", callback_data=f"pay:c:{cd['id']}")] for cd in cards]
    buttons.append(_cancel_row(lang))
    await _edit(client, call, t(lang, "pay_choose_card"), InlineKeyboardMarkup(buttons))


async def _pick_card(client: Client, call: CallbackQuery):
    lang = await _lang(call.from_user.id)
    await call.answer()
    card = await database.get_card(int(call.matches[0].group(1)))
    if not card:
        await _edit(client, call, t(lang, "pay_no_cards"))
        return
    await _show_card(client, call, card, lang)


async def _show_card(client: Client, call: CallbackQuery, card: dict, lang: str):
    uid = call.from_user.id
    s = await database.get_settings()
    payment_flow[uid] = {
        "method": "card",
        "method_detail": f"{card['bank_name']} •• {card['card_number'][-4:]}",
        "amount": str(int(s["price_uzs"])), "currency": "UZS",
        "plan_days": int(s["premium_days"] or 0),   # to'lov paytidagi shartlar
        "details_msg_id": call.message.id,
    }
    await _edit(
        client, call,
        t(lang, "pay_card_details", bank=card["bank_name"], number=card["card_number"],
          owner=card["owner_name"], amount=fmt_uzs(s["price_uzs"])),
        _paid_kb(lang),
    )


# ── Kripto (USDT) ───────────────────────────────────────────
async def _method_crypto(client: Client, call: CallbackQuery):
    uid = call.from_user.id
    lang = await _lang(uid)
    await call.answer()
    tokens = await database.list_tokens()
    if not tokens:
        await _edit(client, call, t(lang, "pay_no_tokens"))
        return
    if len(tokens) == 1:
        await _show_networks(client, call, tokens[0], lang)
        return
    buttons = [[InlineKeyboardButton(tk["name"], callback_data=f"pay:t:{tk['id']}")] for tk in tokens]
    buttons.append(_cancel_row(lang))
    await _edit(client, call, t(lang, "pay_choose_token"), InlineKeyboardMarkup(buttons))


async def _pick_token(client: Client, call: CallbackQuery):
    lang = await _lang(call.from_user.id)
    await call.answer()
    token = await database.get_token(int(call.matches[0].group(1)))
    if not token:
        await _edit(client, call, t(lang, "pay_no_tokens"))
        return
    await _show_networks(client, call, token, lang)


async def _show_networks(client: Client, call: CallbackQuery, token: dict, lang: str):
    networks = await database.list_networks(token["id"])
    if not networks:
        await _edit(client, call, t(lang, "pay_no_networks"))
        return
    if len(networks) == 1:
        await _show_network(client, call, token, networks[0], lang)
        return
    buttons = [[InlineKeyboardButton(n["network_name"], callback_data=f"pay:n:{n['id']}")] for n in networks]
    buttons.append(_cancel_row(lang))
    await _edit(client, call, t(lang, "pay_choose_network", token=token["name"]), InlineKeyboardMarkup(buttons))


async def _pick_network(client: Client, call: CallbackQuery):
    lang = await _lang(call.from_user.id)
    await call.answer()
    network = await database.get_network(int(call.matches[0].group(1)))
    token = await database.get_token(network["token_id"]) if network else None
    if not network or not token:
        await _edit(client, call, t(lang, "pay_no_networks"))
        return
    await _show_network(client, call, token, network, lang)


async def _show_network(client: Client, call: CallbackQuery, token: dict, network: dict, lang: str):
    uid = call.from_user.id
    s = await database.get_settings()
    amount = fmt_usdt(s["price_usdt"])
    payment_flow[uid] = {
        "method": "crypto",
        "method_detail": f"{token['name']} / {network['network_name']}",
        "amount": amount, "currency": "USDT",
        "plan_days": int(s["premium_days"] or 0),
        "details_msg_id": call.message.id,
    }
    key = "pay_network_details_usdt" if token["name"].strip().upper() == "USDT" else "pay_network_details_other"
    await _edit(
        client, call,
        t(lang, key, token=token["name"], network=network["network_name"],
          address=network["address"], amount=amount),
        _paid_kb(lang),
    )


# ── "To'ladim" -> chek so'rash ──────────────────────────────
async def _i_paid(client: Client, call: CallbackQuery):
    uid = call.from_user.id
    lang = await _lang(uid)
    await call.answer()
    flow = payment_flow.get(uid)
    if not flow or "method_detail" not in flow:
        return
    await _safe_delete(client, call.message.chat.id, flow.get("ask_msg_id"))  # ikki marta bosilgan bo'lsa
    ask = await client.send_message(
        call.message.chat.id, t(lang, "pay_ask_receipt"), reply_markup=InlineKeyboardMarkup([_cancel_row(lang)])
    )
    flow.update(awaiting_receipt=True, ask_msg_id=ask.id, ts=time.monotonic())


async def _cancel(client: Client, call: CallbackQuery):
    uid = call.from_user.id
    lang = await _lang(uid)
    flow = payment_flow.pop(uid, None) or {}
    await call.answer(t(lang, "pay_cancelled"))
    chat_id = call.message.chat.id
    for mid in {call.message.id, flow.get("details_msg_id"), flow.get("ask_msg_id")}:
        await _safe_delete(client, chat_id, mid)


# ── Chek qabul qilish ───────────────────────────────────────
def _is_pdf(doc) -> bool:
    return bool(doc) and (doc.mime_type == "application/pdf" or (doc.file_name or "").lower().endswith(".pdf"))


async def _receive_receipt(client: Client, message: Message):
    """Rasm/PDF -- handlers.py'dagi rasm->PDF va PDF o'qish handlerlaridan
    OLDIN ishlaydi (group=-1). Foydalanuvchi chek kutilayotgan holatda
    bo'lmasa -- xabar tegmasdan keyingi handlerlarga uzatiladi."""
    uid = message.from_user.id
    gid = message.media_group_id

    if gid and _receipt_groups.get(uid) == gid:
        message.stop_propagation()  # allaqachon qabul qilingan chek albomining davomi

    flow = payment_flow.get(uid)
    if not flow or not flow.get("awaiting_receipt"):
        raise ContinuePropagation
    if time.monotonic() - flow.get("ts", 0) > config.RECEIPT_WAIT_TIMEOUT:
        payment_flow.pop(uid, None)  # kutish muddati tugadi -- oddiy fayl sifatida davom etadi
        raise ContinuePropagation

    if message.photo:
        file_id, receipt_type = message.photo.file_id, "photo"
    elif _is_pdf(message.document):
        file_id, receipt_type = message.document.file_id, "document"
    else:
        lang = await _lang(uid)
        await message.reply(t(lang, "pay_invalid_receipt"))
        message.stop_propagation()

    # Shu yerdan boshlab await'gacha hech narsa kutilmaydi: albomning
    # ikkinchi rasmi parallel kelsa ham, ikkinchi to'lov yaratilmaydi.
    payment_flow.pop(uid, None)
    if gid:
        _receipt_groups[uid] = gid

    lang = await _lang(uid)
    payment_id = await database.create_payment(
        user_id=uid, method=flow["method"], method_detail=flow["method_detail"],
        amount=flow["amount"], currency=flow["currency"],
        receipt_file_id=file_id, receipt_type=receipt_type, plan_days=flow["plan_days"],
    )
    chat_id = message.chat.id
    await _safe_delete(client, chat_id, flow.get("ask_msg_id"))
    await _safe_delete(client, chat_id, flow.get("details_msg_id"))
    await client.send_message(chat_id, t(lang, "pay_submitted", payment_id=payment_id))
    await notify_admins(client, payment_id, message.from_user)

    # Chek qabul qilindi -- fayl "yangi rasm/PDF" sifatida PDF jarayoniga
    # tushib ketmasligi uchun xabar shu yerda TO'XTATILADI.
    message.stop_propagation()


# ════════════════════════════════════════════════════════════
#  PREMIUM MUDDATI TUGAGANINI XABARLASH (fon-vazifa)
# ════════════════════════════════════════════════════════════
EXPIRY_CHECK_INTERVAL = 600  # soniya


async def expiry_loop(client: Client) -> None:
    """Har 10 daqiqada: muddati tugagan premiumlarni bazadan olib
    tashlaydi (limitlar o'sha zahoti oddiy holatga qaytadi -- bu
    database.get_premium() da ham hisobga olingan) va foydalanuvchiga
    xabar yuboradi."""
    while True:
        try:
            expired = await database.expire_premium_users()
            for uid in expired:
                try:
                    lang = await _lang(uid)
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton(t(lang, "premium_btn_get"), callback_data="premium:info")
                    ]])
                    await client.send_message(uid, t(lang, "premium_expired_user"), reply_markup=kb)
                except Exception:
                    pass  # foydalanuvchi botni bloklagan bo'lishi mumkin
                await asyncio.sleep(0.05)
            if expired:
                logger.info("Premium muddati tugadi: %d ta foydalanuvchi", len(expired))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Premium muddatini tekshirishda xato: %s", e)
        await asyncio.sleep(EXPIRY_CHECK_INTERVAL)


# ════════════════════════════════════════════════════════════
#  RO'YXATDAN O'TKAZISH
# ════════════════════════════════════════════════════════════
def register(client: Client) -> None:

    @client.on_callback_query(filters.regex(r"^pay:buy$"))
    async def pay_buy_cb(c, call):
        await _start_buy(c, call)

    @client.on_callback_query(filters.regex(r"^pay:m:card$"))
    async def pay_method_card_cb(c, call):
        await _method_card(c, call)

    @client.on_callback_query(filters.regex(r"^pay:m:crypto$"))
    async def pay_method_crypto_cb(c, call):
        await _method_crypto(c, call)

    @client.on_callback_query(filters.regex(r"^pay:c:(\d+)$"))
    async def pay_card_cb(c, call):
        await _pick_card(c, call)

    @client.on_callback_query(filters.regex(r"^pay:t:(\d+)$"))
    async def pay_token_cb(c, call):
        await _pick_token(c, call)

    @client.on_callback_query(filters.regex(r"^pay:n:(\d+)$"))
    async def pay_network_cb(c, call):
        await _pick_network(c, call)

    @client.on_callback_query(filters.regex(r"^pay:paid$"))
    async def pay_paid_cb(c, call):
        await _i_paid(c, call)

    @client.on_callback_query(filters.regex(r"^pay:cancel$"))
    async def pay_cancel_cb(c, call):
        await _cancel(c, call)

    # group=-1 -- handlers.py'dagi umumiy rasm/PDF handlerlaridan OLDIN
    @client.on_message((filters.photo | filters.document) & filters.private, group=-1)
    async def receipt_msg(c, m):
        await _receive_receipt(c, m)
