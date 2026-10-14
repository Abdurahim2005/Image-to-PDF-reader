# ════════════════════════════════════════════════════════════
#  ADMIN PANEL: 🌟 PREMIUM va 💳 TO'LOV SOZLAMALARI
#
#  🌟 Premium
#     ⚙️ Premium sozlamalari -- premium obunachilar uchun limitlar
#        (kunlik PDF yaratish, kunlik PDF o'qish, 1 PDF uchun max rasm;
#        har biri 0 = cheksiz) va premium MUDDATI (kun; 0 = cheksiz)
#     👥 Premium obunachilari -- faol obunachilar ro'yxati, qo'lda
#        premium berish / olib tashlash
#
#  💳 To'lov sozlamalari
#     💰 Narxlar (UZS va USDT)  ·  💳 Kartalar  ·  🪙 Kripto (token ->
#     tarmoqlar)  ·  📥 Kutilayotgan to'lovlar (chekni ko'rib
#     tasdiqlash / rad etish)
#
#  Callback prefiksi "ap:" -- admin_panel.py'ning "adm:" routeridan
#  alohida, shuning uchun ikkalasi bir-biriga xalaqit bermaydi.
# ════════════════════════════════════════════════════════════
import logging
import math

from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Message,
)

import database
import handlers  # is_admin, admin_flow
import premium
from i18n import t
from premium import (
    clean_name, fmt_duration, fmt_limit, fmt_until, fmt_usdt, fmt_uzs,
)

logger = logging.getLogger(__name__)

# admin tugmasi -> (settings ustuni, sarlavha kaliti)
_VALUE_FIELDS = {
    "create": ("premium_create_limit", "ap_field_create"),
    "read": ("premium_read_limit", "ap_field_read"),
    "images": ("premium_max_images", "ap_field_images"),
    "days": ("premium_days", "ap_field_days"),
}
_SUBS_PAGE = 10


async def _lang(uid: int) -> str:
    return await database.get_user_language(uid)


def _back(lang: str, to: str) -> list:
    return [Btn(t(lang, "admin_btn_back"), callback_data=to)]


# ════════════════════════════════════════════════════════════
#  EKRANLAR -- har biri (matn, klaviatura) qaytaradi
# ════════════════════════════════════════════════════════════
async def menu_screen(lang: str):
    s = await database.get_settings()
    text = t(
        lang, "ap_premium_menu_text", subs=await database.count_premium_users(),
        price_uzs=fmt_uzs(s["price_uzs"]), price_usdt=fmt_usdt(s["price_usdt"]),
        duration=fmt_duration(lang, s["premium_days"]),
    )
    return text, Kb([
        [Btn(t(lang, "ap_btn_settings"), callback_data="ap:set")],
        [Btn(t(lang, "ap_btn_subscribers"), callback_data="ap:subs:0")],
        _back(lang, "adm:panel"),
    ])


async def settings_screen(lang: str):
    s = await database.get_settings()
    text = t(
        lang, "ap_settings_text",
        p_create=fmt_limit(lang, s["premium_create_limit"]), p_read=fmt_limit(lang, s["premium_read_limit"]),
        p_images=fmt_limit(lang, s["premium_max_images"]), duration=fmt_duration(lang, s["premium_days"]),
    )
    return text, Kb([
        [Btn(t(lang, "ap_btn_create"), callback_data="ap:v:create")],
        [Btn(t(lang, "ap_btn_read"), callback_data="ap:v:read")],
        [Btn(t(lang, "ap_btn_images"), callback_data="ap:v:images")],
        [Btn(t(lang, "ap_btn_days"), callback_data="ap:v:days")],
        _back(lang, "ap:menu"),
    ])


async def subs_screen(lang: str, offset: int):
    rows, total = await database.list_premium_users(offset, _SUBS_PAGE)
    text = t(lang, "ap_subs_title", total=total) if total else t(lang, "ap_subs_empty")
    buttons = []
    for r in rows:
        name = user_label_from_row(r)
        buttons.append([Btn(f"{name} · {fmt_until(lang, r['expires_at'])}",
                            callback_data=f"ap:ucard:{r['telegram_id']}:{offset}")])
    nav = []
    if offset > 0:
        nav.append(Btn("⬅️", callback_data=f"ap:subs:{max(0, offset - _SUBS_PAGE)}"))
    if offset + _SUBS_PAGE < total:
        nav.append(Btn("➡️", callback_data=f"ap:subs:{offset + _SUBS_PAGE}"))
    if nav:
        buttons.append(nav)
    buttons.append([Btn(t(lang, "ap_btn_grant"), callback_data="ap:grant")])
    buttons.append(_back(lang, "ap:menu"))
    return text, Kb(buttons)


def user_label_from_row(r: dict) -> str:
    return premium.user_label(r.get("username"), r.get("first_name"), r["telegram_id"])


async def pay_menu_screen(lang: str):
    s = await database.get_settings()
    pending = await database.count_pending_payments()
    text = t(
        lang, "ap_pay_menu_text", pending=pending,
        cards=len(await database.list_cards()), tokens=len(await database.list_tokens()),
        price_uzs=fmt_uzs(s["price_uzs"]), price_usdt=fmt_usdt(s["price_usdt"]),
    )
    return text, Kb([
        [Btn(t(lang, "ap_btn_prices"), callback_data="ap:price")],
        [Btn(t(lang, "ap_btn_cards"), callback_data="ap:cards"),
         Btn(t(lang, "ap_btn_crypto"), callback_data="ap:crypto")],
        [Btn(t(lang, "ap_btn_queue", n=pending), callback_data="ap:queue")],
        _back(lang, "adm:panel"),
    ])


async def prices_screen(lang: str):
    s = await database.get_settings()
    text = t(
        lang, "ap_prices_text", price_uzs=fmt_uzs(s["price_uzs"]), price_usdt=fmt_usdt(s["price_usdt"]),
        duration=fmt_duration(lang, s["premium_days"]),
    )
    return text, Kb([
        [Btn(t(lang, "ap_btn_price_uzs"), callback_data="ap:pr:uzs")],
        [Btn(t(lang, "ap_btn_price_usdt"), callback_data="ap:pr:usdt")],
        _back(lang, "ap:pay"),
    ])


async def cards_screen(lang: str):
    cards = await database.list_cards()
    buttons = [[Btn(f"{c['bank_name']} •• {c['card_number'][-4:]}  🗑", callback_data=f"ap:cd:{c['id']}")]
               for c in cards]
    buttons.append([Btn(t(lang, "ap_btn_add_card"), callback_data="ap:ca")])
    buttons.append(_back(lang, "ap:pay"))
    return t(lang, "ap_cards_text" if cards else "ap_cards_empty"), Kb(buttons)


async def crypto_screen(lang: str):
    tokens = await database.list_tokens()
    buttons = [[Btn(f"{tk['name']}  🗑", callback_data=f"ap:td:{tk['id']}"),
                Btn(t(lang, "ap_btn_networks"), callback_data=f"ap:tn:{tk['id']}")] for tk in tokens]
    buttons.append([Btn(t(lang, "ap_btn_add_token"), callback_data="ap:ta")])
    buttons.append(_back(lang, "ap:pay"))
    return t(lang, "ap_tokens_text" if tokens else "ap_tokens_empty"), Kb(buttons)


async def networks_screen(lang: str, token_id: int):
    token = await database.get_token(token_id)
    if not token:
        return await crypto_screen(lang)
    nets = await database.list_networks(token_id)
    buttons = [[Btn(f"{n['network_name']}  🗑", callback_data=f"ap:nd:{n['id']}:{token_id}")] for n in nets]
    buttons.append([Btn(t(lang, "ap_btn_add_network"), callback_data=f"ap:na:{token_id}")])
    buttons.append(_back(lang, "ap:crypto"))
    key = "ap_networks_text" if nets else "ap_networks_empty"
    return t(lang, key, token=token["name"]), Kb(buttons)


# ════════════════════════════════════════════════════════════
#  YORDAMCHILAR
# ════════════════════════════════════════════════════════════
async def _show(client: Client, call: CallbackQuery, screen) -> None:
    """Ekranni shu xabarning o'rniga chiqaradi (xabar rasm/chek bo'lsa --
    yangisini yuboradi)."""
    text, kb = screen
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except MessageNotModified:
        pass
    except Exception:
        await client.send_message(call.message.chat.id, text, reply_markup=kb)


async def _prompt(client: Client, call: CallbackQuery, uid: int, flow: dict, text: str, back_to: str,
                  extra_rows: list | None = None) -> None:
    """Admindan matn kiritishni so'raydi."""
    handlers.admin_flow[uid] = flow
    lang = await _lang(uid)
    rows = (extra_rows or []) + [_back(lang, back_to)]
    await _show(client, call, (text, Kb(rows)))


def _parse_int(text: str) -> int | None:
    s = (text or "").strip().replace(" ", "").replace(",", "")
    return int(s) if s.isdigit() else None


def _parse_positive_float(text: str) -> float | None:
    s = (text or "").strip().replace(" ", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) and v > 0 else None


def _value_text(lang: str, field: str, value: int) -> str:
    return fmt_duration(lang, value) if field == "days" else fmt_limit(lang, value)


async def _grant(client: Client, target_uid: int) -> str | None:
    """Foydalanuvchiga joriy premium muddati bilan premium beradi va unga
    xabar yuboradi. Qaytaradi: tugash sanasi (None = cheksiz)."""
    s = await database.get_settings()
    expires = await database.apply_premium(target_uid, int(s["premium_days"] or 0))
    try:
        ulang = await _lang(target_uid)
        await client.send_message(target_uid, t(ulang, "ap_grant_notify_user", until=fmt_until(ulang, expires)))
    except Exception:
        pass  # foydalanuvchi botni bloklagan bo'lishi mumkin
    return expires


# ════════════════════════════════════════════════════════════
#  TO'LOVNI TASDIQLASH / RAD ETISH
# ════════════════════════════════════════════════════════════
async def _resolve_payment(client: Client, call: CallbackQuery, lang: str, payment_id: int, approve: bool):
    pay = await database.resolve_payment(payment_id, approve, call.from_user.id)
    if not pay:
        await call.answer(t(lang, "ap_payment_already"), show_alert=True)
        return

    user_id = pay["user_id"]
    ulang = await _lang(user_id)
    expires = None
    if approve:
        try:
            expires = await database.apply_premium(user_id, int(pay["plan_days"] or 0))
        except Exception:
            logger.exception("To'lov №%s tasdiqlandi, lekin premium berib bo'lmadi (uid=%s)", payment_id, user_id)
            await call.answer(t(lang, "ap_grant_failed", id=user_id), show_alert=True)
            return

    # Admin xabarini natija bilan almashtiramiz (tugmalar yo'qoladi)
    result = t(lang, "ap_payment_approved_admin" if approve else "ap_payment_rejected_admin", id=payment_id)
    pending = await database.count_pending_payments()
    kb = Kb([[Btn(t(lang, "ap_btn_next_payment"), callback_data="ap:queue")]]) if pending else None
    try:
        if call.message.caption is not None:
            await call.message.edit_caption(result, reply_markup=kb)
        else:
            await call.message.edit_text(result, reply_markup=kb)
    except Exception:
        pass

    try:
        if approve:
            await client.send_message(user_id, t(ulang, "pay_approved_user", until=fmt_until(ulang, expires)))
        else:
            await client.send_message(user_id, t(ulang, "pay_rejected_user", payment_id=payment_id))
    except Exception as e:
        logger.warning("Foydalanuvchiga (%s) to'lov natijasini yuborib bo'lmadi: %s", user_id, e)
    await call.answer()


# ════════════════════════════════════════════════════════════
#  MATN KIRITISH (admin_panel.handle_admin_text shu yerga uzatadi)
# ════════════════════════════════════════════════════════════
async def handle_text(client: Client, message: Message, flow: dict, lang: str) -> bool:
    """True qaytarsa -- xabar ishlatilgan. Noto'g'ri kiritilsa jarayon
    BEKOR QILINMAYDI: admin qayta urinib ko'radi (yoki 'Orqaga' bosadi)."""
    action = flow.get("action", "")
    if not action.startswith("ap_"):
        return False
    uid = message.from_user.id
    chat_id = message.chat.id
    text = (message.text or "").strip()

    # ── Premium limit / muddat ──
    if action == "ap_value":
        value = _parse_int(text)
        if value is None:
            await message.reply(t(lang, "ap_invalid_number"))
            return True
        field = flow["field"]
        column, label_key = _VALUE_FIELDS[field]
        await database.set_premium_setting(column, value)
        handlers.admin_flow.pop(uid, None)
        await message.reply(t(lang, "ap_value_set", what=t(lang, label_key), value=_value_text(lang, field, value)))
        scr = await settings_screen(lang)
        await client.send_message(chat_id, scr[0], reply_markup=scr[1])
        return True

    # ── Narx ──
    if action == "ap_price":
        field = flow["field"]
        if field == "uzs":
            value = _parse_int(text)
            value = value if value and value > 0 else None
        else:
            value = _parse_positive_float(text)
        if value is None:
            await message.reply(t(lang, "ap_invalid_price"))
            return True
        await database.set_premium_setting("price_uzs" if field == "uzs" else "price_usdt", value)
        handlers.admin_flow.pop(uid, None)
        await message.reply(t(lang, "ap_price_set"))
        scr = await prices_screen(lang)
        await client.send_message(chat_id, scr[0], reply_markup=scr[1])
        return True

    # ── Qo'lda premium berish (ID bo'yicha) ──
    if action == "ap_grant":
        target = _parse_int(text)
        if target is None or not await database.get_user(target):
            await message.reply(t(lang, "ap_user_not_found"))
            return True
        handlers.admin_flow.pop(uid, None)
        expires = await _grant(client, target)
        await message.reply(
            t(lang, "ap_grant_done", id=target, until=fmt_until(lang, expires)),
            reply_markup=Kb([_back(lang, "ap:subs:0")]),
        )
        return True

    # ── Karta qo'shish: bank -> raqam -> egasi ──
    if action == "ap_card":
        step = flow["step"]
        if step == "bank":
            flow.update(bank_name=text, step="number")
            await message.reply(t(lang, "ap_ask_card_number"))
        elif step == "number":
            flow.update(card_number=text, step="owner")
            await message.reply(t(lang, "ap_ask_card_owner"))
        else:
            await database.add_card(flow["bank_name"], flow["card_number"], text)
            handlers.admin_flow.pop(uid, None)
            await message.reply(t(lang, "ap_card_added"))
            scr = await cards_screen(lang)
            await client.send_message(chat_id, scr[0], reply_markup=scr[1])
        return True

    # ── Token qo'shish ──
    if action == "ap_token":
        if not text:
            return True
        await database.add_crypto_token(text)
        handlers.admin_flow.pop(uid, None)
        await message.reply(t(lang, "ap_token_added", name=text.upper()))
        scr = await crypto_screen(lang)
        await client.send_message(chat_id, scr[0], reply_markup=scr[1])
        return True

    # ── Tarmoq qo'shish: nom -> manzil ──
    if action == "ap_network":
        if flow["step"] == "name":
            flow.update(network_name=text, step="address")
            await message.reply(t(lang, "ap_ask_network_address"))
        else:
            await database.add_network(flow["token_id"], flow["network_name"], text)
            handlers.admin_flow.pop(uid, None)
            await message.reply(t(lang, "ap_network_added"))
            scr = await networks_screen(lang, flow["token_id"])
            await client.send_message(chat_id, scr[0], reply_markup=scr[1])
        return True

    return False


# ════════════════════════════════════════════════════════════
#  CALLBACK ROUTERI ("ap:")
# ════════════════════════════════════════════════════════════
def register(client: Client) -> None:

    @client.on_callback_query(filters.regex(r"^ap:"))
    async def ap_cb(c, call: CallbackQuery):
        uid = call.from_user.id
        lang = await _lang(uid)
        if not handlers.is_admin(uid):
            await call.answer(t(lang, "admin_only"), show_alert=True)
            return

        # Boshqa tugma bosilsa, kutilayotgan matn-kiritish bekor qilinadi
        if str(handlers.admin_flow.get(uid, {}).get("action", "")).startswith("ap_"):
            handlers.admin_flow.pop(uid, None)

        parts = call.data.split(":")
        cmd = parts[1]
        chat_id, msg_id = call.message.chat.id, call.message.id

        # ── 🌟 Premium ──
        if cmd == "menu":
            await call.answer()
            await _show(c, call, await menu_screen(lang))

        elif cmd == "set":
            await call.answer()
            await _show(c, call, await settings_screen(lang))

        elif cmd == "v":  # limit/muddat qiymatini so'rash
            await call.answer()
            field = parts[2]
            _, label_key = _VALUE_FIELDS[field]
            await _prompt(
                c, call, uid, {"action": "ap_value", "field": field},
                t(lang, "ap_ask_value", what=t(lang, label_key)), "ap:set",
                extra_rows=[[Btn(t(lang, "ap_btn_unlimited"), callback_data=f"ap:v0:{field}")]],
            )

        elif cmd == "v0":  # "♾ Cheksiz qilish"
            await call.answer()
            field = parts[2]
            column, _ = _VALUE_FIELDS[field]
            await database.set_premium_setting(column, 0)
            await _show(c, call, await settings_screen(lang))

        elif cmd == "subs":
            await call.answer()
            await _show(c, call, await subs_screen(lang, int(parts[2])))

        elif cmd == "ucard":  # obunachi kartochkasi (orqaga -- ro'yxatga)
            await call.answer()
            import admin_panel
            await admin_panel._show_user_card(
                c, chat_id, lang, int(parts[2]), edit_msg_id=msg_id, back_cb=f"ap:subs:{int(parts[3])}"
            )

        elif cmd == "grant":  # ID bo'yicha premium berish
            await call.answer()
            s = await database.get_settings()
            await _prompt(
                c, call, uid, {"action": "ap_grant"},
                t(lang, "ap_ask_grant_id", duration=fmt_duration(lang, s["premium_days"])), "ap:subs:0",
            )

        elif cmd == "ug":  # foydalanuvchi kartochkasidan -- premium berish
            await call.answer()
            target = int(parts[2])
            await _grant(c, target)
            import admin_panel
            await admin_panel._show_user_card(c, chat_id, lang, target, edit_msg_id=msg_id)

        elif cmd == "ur":  # foydalanuvchi kartochkasidan -- premiumni olish
            target = int(parts[2])
            had = await database.revoke_premium(target)
            await call.answer(t(lang, "ap_revoke_done", id=target) if had else t(lang, "ap_revoke_none"))
            import admin_panel
            await admin_panel._show_user_card(c, chat_id, lang, target, edit_msg_id=msg_id)

        # ── 💳 To'lov sozlamalari ──
        elif cmd == "pay":
            await call.answer()
            await _show(c, call, await pay_menu_screen(lang))

        elif cmd == "price":
            await call.answer()
            await _show(c, call, await prices_screen(lang))

        elif cmd == "pr":  # narxni so'rash
            await call.answer()
            field = parts[2]
            await _prompt(c, call, uid, {"action": "ap_price", "field": field},
                          t(lang, f"ap_ask_price_{field}"), "ap:price")

        elif cmd == "cards":
            await call.answer()
            await _show(c, call, await cards_screen(lang))

        elif cmd == "ca":
            await call.answer()
            await _prompt(c, call, uid, {"action": "ap_card", "step": "bank"}, t(lang, "ap_ask_card_bank"), "ap:cards")

        elif cmd == "cd":
            await database.delete_card(int(parts[2]))
            await call.answer()
            await _show(c, call, await cards_screen(lang))

        elif cmd == "crypto":
            await call.answer()
            await _show(c, call, await crypto_screen(lang))

        elif cmd == "ta":
            await call.answer()
            await _prompt(c, call, uid, {"action": "ap_token"}, t(lang, "ap_ask_token"), "ap:crypto")

        elif cmd == "td":
            await database.delete_token(int(parts[2]))
            await call.answer()
            await _show(c, call, await crypto_screen(lang))

        elif cmd == "tn":  # token tarmoqlari
            await call.answer()
            await _show(c, call, await networks_screen(lang, int(parts[2])))

        elif cmd == "na":
            await call.answer()
            token_id = int(parts[2])
            await _prompt(c, call, uid, {"action": "ap_network", "token_id": token_id, "step": "name"},
                          t(lang, "ap_ask_network_name"), f"ap:tn:{token_id}")

        elif cmd == "nd":
            await database.delete_network(int(parts[2]))
            await call.answer()
            await _show(c, call, await networks_screen(lang, int(parts[3])))

        # ── 📥 Kutilayotgan to'lovlar ──
        elif cmd == "queue":
            await call.answer()
            pending = await database.list_pending_payments(1)
            if not pending:
                await _show(c, call, (t(lang, "ap_queue_empty"), Kb([_back(lang, "ap:pay")])))
            else:
                await premium.send_payment_review(c, chat_id, lang, pending[0])

        elif cmd == "payok":
            await _resolve_payment(c, call, lang, int(parts[2]), True)

        elif cmd == "payno":
            await _resolve_payment(c, call, lang, int(parts[2]), False)

        else:
            await call.answer()
