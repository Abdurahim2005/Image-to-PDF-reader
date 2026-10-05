import asyncio
import logging
import os
import shutil

from pyrogram import Client

import config
from i18n import load_locales
import database
import handlers
import admin_panel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Pyrogram har connect/disconnect/ping/session haqida INFO darajasida
# juda ko'p (foydasiz) log yozadi -- bizga faqat OGOHLANTIRISH va
# XATOLAR kerak, shuning uchun pyrogram'ning o'z logger'ini jimroq
# qilamiz (bizning o'z logimiz -- `logger` -- hali INFO darajasida
# qoladi).
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pyrogram.session").setLevel(logging.WARNING)
logging.getLogger("pyrogram.connection").setLevel(logging.WARNING)

app = Client(
    "pdfbot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
)


async def _startup():
    load_locales()
    await database.init_db()

    # Bot ishga tushganda vaqtinchalik papkani tozalaymiz -- server
    # qayta ishga tushgan bo'lsa, eski chala-yarim fayllar qolmasligi
    # uchun ("xotira" axlat yig'ib qolmasligi).
    shutil.rmtree(config.TEMP_DIR, ignore_errors=True)
    os.makedirs(config.TEMP_DIR, exist_ok=True)

    me = await app.get_me()
    handlers._bot_username_cached = me.username or ""
    if not config.BOT_USERNAME:
        config.BOT_USERNAME = me.username or ""

    handlers.init(app)
    admin_panel.register(app)

    logger.info("Bot ishga tushdi: @%s", me.username)


async def _runner():
    await app.start()
    await _startup()
    logger.info("Bot ishlayapti, to'xtatish uchun Ctrl+C")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        app.run(_runner())
    except KeyboardInterrupt:
        pass
