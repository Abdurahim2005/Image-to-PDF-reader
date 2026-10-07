# ════════════════════════════════════════════════════════════
#  BAZA QATLAMI -- Turso (libSQL) ustida ishlaydi.
#
#  TEZLIK UCHUN: `libsql_experimental` (EMBEDDED REPLICA rejimi)
#  ishlatiladi -- bu HAR BIR so'rovni internet orqali Turso serveriga
#  yubormaydi, balki LOKAL faylga (`/tmp/pdfbot_replica.db`) yozadi va
#  o'qiydi (millisekundlar), fon vazifasi esa muntazam ravishda shu
#  lokal nusxani Turso bilan sinxronlaydi (`conn.sync()`). Natijada
#  har bir so'rov tarmoq kechikishisiz, deyarli zudlik bilan ishlaydi
#  -- xuddi oddiy SQLite kabi.
#
#  `libsql_experimental` SINXRON kutubxona (async emas), shuning uchun
#  `_ThreadedClient.execute()` har bir chaqiruvni alohida thread'da
#  (`asyncio.to_thread`) bajaradi -- bu orqali butun bot asinxron
#  (pyrogram) bilan muammosiz ishlayveradi, va `await c.execute(...)`
#  chaqiruvlari butun faylda O'ZGARMAYDI.
#
#  Jadvallar:
#   users            -- har bir foydalanuvchi + statistikasi
#   pending_files    -- "xotira": vaqtincha saqlangan, hali PDF
#                        bo'lmagan rasm to'plamlari (xatolik bo'lib
#                        tozalanmay qolganlarni admin ko'rishi uchun)
#   daily_stats      -- har kun uchun bitta qator (7-kunlik grafik)
#   required_channels -- majburiy obuna kanallari (tg: va ext: turlari)
#   channel_joins    -- kim qaysi kanalga "obuna" deb belgilangan
#   settings         -- global sozlamalar (limitlar)
#   admin_messages   -- foydalanuvchi <-> admin xat almashinuvi bog'lash
# ════════════════════════════════════════════════════════════
import asyncio
import logging
import threading
import time

import libsql_experimental as libsql

import config

logger = logging.getLogger(__name__)

_LOCAL_DB_PATH = "/tmp/pdfbot_replica.db"

_raw_conn = None
_conn_lock = threading.Lock()  # libsql_experimental connection THREAD-SAFE emas


def _get_raw_conn():
    """Lokal (embedded replica) ulanishni bir martalik yaratadi.
    DIQQAT: bu funksiya faqat worker thread ichidan chaqiriladi
    (`asyncio.to_thread` orqali), asosiy event loop'da emas."""
    global _raw_conn
    if _raw_conn is None:
        _raw_conn = libsql.connect(
            _LOCAL_DB_PATH,
            sync_url=config.TURSO_DATABASE_URL,
            auth_token=config.TURSO_AUTH_TOKEN,
        )
        _raw_conn.sync()
        logger.info("Turso embedded replica ulandi: %s", _LOCAL_DB_PATH)
    return _raw_conn


class _ThreadedResult:
    """`libsql_experimental`ning cursor-natijasini `libsql_client`
    bilan BIR XIL interfeysga keltiradi (`.rows`, `.columns`) --
    shunda butun database.py fayli O'ZGARMAYDI."""

    __slots__ = ("rows", "columns")

    def __init__(self, rows, columns):
        self.rows = rows
        self.columns = columns


def _execute_sync(sql: str, params: list, return_last_rowid: bool = False):
    """Worker thread ichida chaqiriladi -- haqiqiy sinxron SQL
    so'rovni bajaradi. Lock bilan himoyalangan, chunki bir connection
    bir vaqtda faqat bitta so'rovni bajarishi kerak.

    `return_last_rowid=True` bo'lsa -- INSERT'dan keyin, SHU LOCK
    ICHIDA (boshqa yozish orada kirib ketmasligi uchun)
    `last_insert_rowid()`ni ham so'rab, natija sifatida qaytaradi."""
    with _conn_lock:
        conn = _get_raw_conn()
        cur = conn.execute(sql, tuple(params or ()))
        try:
            rows = cur.fetchall()
        except Exception:
            rows = []
        columns = [d[0] for d in cur.description] if cur.description else []
        # YOZISH so'rovlari (INSERT/UPDATE/DELETE) uchun darhol commit
        # qilamiz -- libsql_experimental avtomatik committ qilmaydi.
        try:
            conn.commit()
        except Exception:
            pass
        if return_last_rowid:
            rowid_cur = conn.execute("SELECT last_insert_rowid()")
            rows = rowid_cur.fetchall()
            columns = ["last_insert_rowid()"]
        return _ThreadedResult(rows, columns)


class _ThreadedClient:
    """`libsql_client.Client`ning asinxron `execute()` interfeysini
    taqlid qiladi, lekin orqada HAQIQIY SINXRON `libsql_experimental`
    ulanishini `asyncio.to_thread` orqali chaqiradi. Shu orqali butun
    loyihadagi `await c.execute(sql, params)` chaqiruvlari o'zgarishsiz
    qoladi, lekin har bir so'rov endi tarmoq orqali EMAS, balki LOKAL
    fayldan javob oladi -- bir necha millisekund ichida."""

    async def execute(self, sql: str, params: list | None = None, return_last_rowid: bool = False):
        params = params or []
        last_exc = None
        for attempt in range(3):
            try:
                return await asyncio.to_thread(_execute_sync, sql, params, return_last_rowid)
            except Exception as e:
                last_exc = e
                logger.warning("Baza so'rovi xato berdi (urinish %d/3): %s", attempt + 1, e)
                await asyncio.sleep(0.3 * (attempt + 1))
                # Ulanish buzilgan bo'lishi mumkin -- qayta yaratishga
                # majburlaymiz (keyingi urinishda _get_raw_conn yangi
                # ulanish ochadi).
                global _raw_conn
                with _conn_lock:
                    _raw_conn = None
        raise last_exc


async def _sync_loop():
    """Fon vazifasi -- har necha soniyada lokal nusxani Turso bilan
    sinxronlaydi (yozilgan o'zgarishlarni serverga yuboradi, serverda
    boshqa joydan kelgan o'zgarishlarni oladi). Bu bo'lmasa, lokal
    yozishlar hech qachon Turso'ga yetib bormaydi."""
    while True:
        await asyncio.sleep(5)
        try:
            await asyncio.to_thread(_sync_now)
        except Exception as e:
            logger.warning("Turso sync xato berdi: %s", e)


def _sync_now():
    with _conn_lock:
        if _raw_conn is not None:
            _raw_conn.sync()


_sync_task: asyncio.Task | None = None


def start_background_sync() -> None:
    """main.py chaqiradi -- bot ishga tushganda fon-sinxronlashni
    boshlaydi."""
    global _sync_task
    if _sync_task is None:
        _sync_task = asyncio.create_task(_sync_loop())


_client: "_ThreadedClient | None" = None


def get_client() -> "_ThreadedClient":
    global _client
    if _client is None:
        _client = _ThreadedClient()
    return _client


async def ping_database() -> bool:
    """Admin panel -- 'Bazani tekshirish' tugmasi uchun. Lokal
    so'rovning o'zi kam narsa bildiradi (lokal fayl har doim ishlaydi),
    shuning uchun aynan TURSO SERVERI bilan ulanishni tekshirish uchun
    `sync()`ni ham chaqiramiz -- agar server bilan ulanish bo'lmasa,
    bu xato beradi."""
    try:
        await asyncio.to_thread(_sync_now)
        c = get_client()
        await c.execute("SELECT 1")
        return True
    except Exception:
        return False


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


async def init_db() -> None:
    """Bot ishga tushganda bir marta chaqiriladi -- barcha jadvallarni
    (agar yo'q bo'lsa) yaratadi. Turso/libSQL SQLite sintaksisini
    qo'llab-quvvatlaydi, shuning uchun oddiy SQLite DDL ishlatiladi."""
    c = get_client()

    await c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id       INTEGER PRIMARY KEY,
            username          TEXT DEFAULT '',
            first_name        TEXT DEFAULT '',
            language          TEXT DEFAULT 'uz',
            joined_at         TEXT DEFAULT '',
            joined_date       TEXT DEFAULT '',
            is_banned         INTEGER DEFAULT 0,
            images_sent       INTEGER DEFAULT 0,
            pdfs_created      INTEGER DEFAULT 0,
            pdfs_created_today INTEGER DEFAULT 0,
            pdfs_created_date TEXT DEFAULT '',
            pdfs_read         INTEGER DEFAULT 0,
            pdfs_read_today   INTEGER DEFAULT 0,
            pdfs_read_date    TEXT DEFAULT '',
            bytes_processed   INTEGER DEFAULT 0,
            daily_create_limit INTEGER DEFAULT 0,
            daily_read_limit  INTEGER DEFAULT 0,
            max_images_per_pdf INTEGER DEFAULT 0
        )
    """)
    # Eski bazalarda `max_images_per_pdf` ustuni bo'lmasligi mumkin --
    # `CREATE TABLE IF NOT EXISTS` mavjud jadvalni o'zgartirmaydi,
    # shuning uchun bir martalik ALTER TABLE orqali qo'shamiz.
    try:
        await c.execute("ALTER TABLE users ADD COLUMN max_images_per_pdf INTEGER DEFAULT 0")
    except Exception:
        pass  # ustun allaqachon mavjud

    await c.execute("""
        CREATE TABLE IF NOT EXISTS pending_files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            file_path   TEXT NOT NULL,
            size_bytes  INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT ''
        )
    """)

    await c.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            day            TEXT PRIMARY KEY,
            new_users      INTEGER DEFAULT 0,
            pdfs_created   INTEGER DEFAULT 0,
            pdfs_read      INTEGER DEFAULT 0,
            images_sent    INTEGER DEFAULT 0,
            bytes_processed INTEGER DEFAULT 0
        )
    """)
    try:
        await c.execute("ALTER TABLE daily_stats ADD COLUMN bytes_processed INTEGER DEFAULT 0")
    except Exception:
        pass

    await c.execute("""
        CREATE TABLE IF NOT EXISTS required_channels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id  TEXT NOT NULL,
            title       TEXT DEFAULT '',
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT ''
        )
    """)

    await c.execute("""
        CREATE TABLE IF NOT EXISTS channel_joins (
            channel_row_id INTEGER NOT NULL,
            telegram_id    INTEGER NOT NULL,
            joined_at      TEXT DEFAULT '',
            PRIMARY KEY (channel_row_id, telegram_id)
        )
    """)

    await c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id                   INTEGER PRIMARY KEY CHECK (id = 1),
            default_create_limit INTEGER DEFAULT 0,
            default_read_limit   INTEGER DEFAULT 0,
            max_images_per_pdf   INTEGER DEFAULT 0
        )
    """)
    await c.execute(
        "INSERT OR IGNORE INTO settings (id, default_create_limit, default_read_limit, max_images_per_pdf) "
        "VALUES (1, 0, 0, 0)"
    )

    await c.execute("""
        CREATE TABLE IF NOT EXISTS admin_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id     INTEGER NOT NULL,
            admin_msg_id    INTEGER NOT NULL,
            created_at      TEXT DEFAULT ''
        )
    """)


# ════════════════════════════════════════════════════════════
#  FOYDALANUVCHILAR
# ════════════════════════════════════════════════════════════
async def upsert_user(telegram_id: int, username: str, first_name: str) -> bool:
    """Foydalanuvchini qo'shadi (agar yangi bo'lsa) yoki ism/username'ni
    yangilaydi. Qaytaradi: True -- agar bu YANGI (hozirgina qo'shilgan)
    foydalanuvchi bo'lsa, aks holda False (statistikaga 'bugun qo'shildi'
    deb yozish kerak-emasligini bilish uchun)."""
    c = get_client()
    row = await c.execute(
        "SELECT telegram_id FROM users WHERE telegram_id = ?", [telegram_id]
    )
    is_new = len(row.rows) == 0
    if is_new:
        today = _today()
        settings = await get_settings()
        await c.execute(
            "INSERT INTO users (telegram_id, username, first_name, language, joined_at, joined_date, "
            "daily_create_limit, daily_read_limit) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [telegram_id, username or "", first_name or "", config.DEFAULT_LANGUAGE, _now(), today,
             settings["default_create_limit"], settings["default_read_limit"]],
        )
        await bump_daily_stat("new_users")
    else:
        await c.execute(
            "UPDATE users SET username = ?, first_name = ? WHERE telegram_id = ?",
            [username or "", first_name or "", telegram_id],
        )
    return is_new


async def get_user(telegram_id: int) -> dict | None:
    c = get_client()
    res = await c.execute("SELECT * FROM users WHERE telegram_id = ?", [telegram_id])
    if not res.rows:
        return None
    return dict(zip(res.columns, res.rows[0]))


async def get_user_language(telegram_id: int) -> str:
    user = await get_user(telegram_id)
    return (user or {}).get("language") or config.DEFAULT_LANGUAGE


async def set_user_language(telegram_id: int, language: str) -> None:
    c = get_client()
    await c.execute(
        "UPDATE users SET language = ? WHERE telegram_id = ?", [language, telegram_id]
    )


async def is_banned(telegram_id: int) -> bool:
    user = await get_user(telegram_id)
    return bool(user and user.get("is_banned"))


async def set_banned(telegram_id: int, banned: bool) -> None:
    c = get_client()
    await c.execute(
        "UPDATE users SET is_banned = ? WHERE telegram_id = ?", [1 if banned else 0, telegram_id]
    )


async def set_user_limit(telegram_id: int, kind: str, value: int) -> None:
    """`kind`: 'create' | 'read' | 'max_images' -- shu foydalanuvchiga
    SHAXSIY limit qo'yadi (0 = cheklovsiz / global sozlamadan foydalanish)."""
    col_map = {
        "create": "daily_create_limit",
        "read": "daily_read_limit",
        "max_images": "max_images_per_pdf",
    }
    col = col_map[kind]
    c = get_client()
    await c.execute(f"UPDATE users SET {col} = ? WHERE telegram_id = ?", [value, telegram_id])


async def _reset_daily_counter_if_needed(telegram_id: int, field_count: str, field_date: str) -> None:
    """Agar foydalanuvchining 'bugungi' hisoblagichi ESKI sanaga tegishli
    bo'lsa -- 0'ga tushiradi va sanani bugungiga yangilaydi. Har bir
    +1 qilishdan OLDIN chaqiriladi, shuning uchun alohida fon-jarayon
    (cron) kerak emas -- "lazy reset"."""
    user = await get_user(telegram_id)
    if not user:
        return
    if user.get(field_date) != _today():
        c = get_client()
        await c.execute(
            f"UPDATE users SET {field_count} = 0, {field_date} = ? WHERE telegram_id = ?",
            [_today(), telegram_id],
        )


async def get_max_images_per_pdf(telegram_id: int) -> int:
    """0 = cheklovsiz. Foydalanuvchining SHAXSIY limiti bo'lsa (0'dan
    katta) -- shu ishlatiladi; bo'lmasa global sozlamadagi standart
    qiymat qaytariladi."""
    user = await get_user(telegram_id)
    personal = (user or {}).get("max_images_per_pdf") or 0
    if personal > 0:
        return personal
    s = await get_settings()
    return s.get("max_images_per_pdf") or 0


async def can_create_pdf(telegram_id: int) -> tuple[bool, int, int]:
    """Qaytaradi: (ruxsat_bor, bugungi_son, limit). limit=0 -- cheklovsiz."""
    await _reset_daily_counter_if_needed(telegram_id, "pdfs_created_today", "pdfs_created_date")
    user = await get_user(telegram_id)
    limit = user.get("daily_create_limit") or 0
    today_count = user.get("pdfs_created_today") or 0
    if limit <= 0:
        return True, today_count, 0
    return today_count < limit, today_count, limit


async def can_read_pdf(telegram_id: int) -> tuple[bool, int, int]:
    await _reset_daily_counter_if_needed(telegram_id, "pdfs_read_today", "pdfs_read_date")
    user = await get_user(telegram_id)
    limit = user.get("daily_read_limit") or 0
    today_count = user.get("pdfs_read_today") or 0
    if limit <= 0:
        return True, today_count, 0
    return today_count < limit, today_count, limit


async def record_pdf_created(telegram_id: int, size_bytes: int) -> None:
    await _reset_daily_counter_if_needed(telegram_id, "pdfs_created_today", "pdfs_created_date")
    c = get_client()
    await c.execute(
        "UPDATE users SET pdfs_created = pdfs_created + 1, pdfs_created_today = pdfs_created_today + 1, "
        "bytes_processed = bytes_processed + ? WHERE telegram_id = ?",
        [size_bytes, telegram_id],
    )
    await bump_daily_stat("pdfs_created")
    await bump_daily_stat("bytes_processed", by=size_bytes)


async def record_pdf_read(telegram_id: int, size_bytes: int) -> None:
    await _reset_daily_counter_if_needed(telegram_id, "pdfs_read_today", "pdfs_read_date")
    c = get_client()
    await c.execute(
        "UPDATE users SET pdfs_read = pdfs_read + 1, pdfs_read_today = pdfs_read_today + 1, "
        "bytes_processed = bytes_processed + ? WHERE telegram_id = ?",
        [size_bytes, telegram_id],
    )
    await bump_daily_stat("pdfs_read")
    await bump_daily_stat("bytes_processed", by=size_bytes)


async def record_image_sent(telegram_id: int, size_bytes: int) -> None:
    c = get_client()
    await c.execute(
        "UPDATE users SET images_sent = images_sent + 1, bytes_processed = bytes_processed + ? "
        "WHERE telegram_id = ?",
        [size_bytes, telegram_id],
    )
    await bump_daily_stat("images_sent")
    await bump_daily_stat("bytes_processed", by=size_bytes)


# ════════════════════════════════════════════════════════════
#  KUNLIK STATISTIKA (7-kunlik grafik uchun)
# ════════════════════════════════════════════════════════════
async def bump_daily_stat(field: str, by: int = 1) -> None:
    c = get_client()
    today = _today()
    await c.execute(
        "INSERT INTO daily_stats (day) VALUES (?) ON CONFLICT(day) DO NOTHING", [today]
    )
    await c.execute(f"UPDATE daily_stats SET {field} = {field} + ? WHERE day = ?", [by, today])


async def get_last_n_days_stats(n: int = 7) -> list[dict]:
    c = get_client()
    res = await c.execute(
        "SELECT * FROM daily_stats ORDER BY day DESC LIMIT ?", [n]
    )
    rows = [dict(zip(res.columns, r)) for r in res.rows]
    return list(reversed(rows))


# ════════════════════════════════════════════════════════════
#  UMUMIY STATISTIKA (admin panel bosh sahifasi)
# ════════════════════════════════════════════════════════════
async def get_overview_stats() -> dict:
    """Admin panel -- bosh statistika. JAMI (butun vaqt) va BUGUNGI
    ko'rsatkichlarni alohida qaytaradi: foydalanuvchi, rasm, PDF
    yasash, PDF o'qish, qayta ishlangan hajm."""
    c = get_client()
    today = _today()

    total_users = (await c.execute("SELECT COUNT(*) FROM users")).rows[0][0]
    total_images = (await c.execute("SELECT COALESCE(SUM(images_sent), 0) FROM users")).rows[0][0]
    total_pdfs = (await c.execute("SELECT COALESCE(SUM(pdfs_created), 0) FROM users")).rows[0][0]
    total_read = (await c.execute("SELECT COALESCE(SUM(pdfs_read), 0) FROM users")).rows[0][0]
    total_bytes = (await c.execute("SELECT COALESCE(SUM(bytes_processed), 0) FROM users")).rows[0][0]
    lang_uz = (await c.execute("SELECT COUNT(*) FROM users WHERE language = 'uz'")).rows[0][0]
    lang_en = (await c.execute("SELECT COUNT(*) FROM users WHERE language = 'en'")).rows[0][0]

    today_new = (await c.execute(
        "SELECT COUNT(*) FROM users WHERE joined_date = ?", [today]
    )).rows[0][0]
    today_row = (await c.execute(
        "SELECT new_users, pdfs_created, pdfs_read, images_sent, bytes_processed FROM daily_stats WHERE day = ?", [today]
    )).rows
    if today_row:
        _, today_pdfs, today_read, today_images, today_bytes = today_row[0]
    else:
        today_pdfs, today_read, today_images, today_bytes = 0, 0, 0, 0

    return {
        "total_users": total_users,
        "total_images": total_images,
        "total_pdfs": total_pdfs,
        "total_read": total_read,
        "total_bytes": total_bytes,
        "lang_uz": lang_uz,
        "lang_en": lang_en,
        "today_new": today_new,
        "today_pdfs": today_pdfs,
        "today_read": today_read,
        "today_images": today_images,
        "today_bytes": today_bytes or 0,
    }


async def get_top_creators(limit: int = 10) -> list[dict]:
    c = get_client()
    res = await c.execute(
        "SELECT telegram_id, username, first_name, pdfs_created FROM users "
        "WHERE pdfs_created > 0 ORDER BY pdfs_created DESC LIMIT ?", [limit]
    )
    return [dict(zip(res.columns, r)) for r in res.rows]


async def get_top_readers(limit: int = 10) -> list[dict]:
    c = get_client()
    res = await c.execute(
        "SELECT telegram_id, username, first_name, pdfs_read FROM users "
        "WHERE pdfs_read > 0 ORDER BY pdfs_read DESC LIMIT ?", [limit]
    )
    return [dict(zip(res.columns, r)) for r in res.rows]


async def search_users(query: str, limit: int = 10) -> list[dict]:
    """ID (raqam) yoki username (@ bilan yoki bo'lmasa) bo'yicha qidiradi."""
    c = get_client()
    query = query.strip().lstrip("@")
    if query.isdigit():
        res = await c.execute(
            "SELECT * FROM users WHERE telegram_id = ? LIMIT ?", [int(query), limit]
        )
    else:
        res = await c.execute(
            "SELECT * FROM users WHERE username LIKE ? LIMIT ?", [f"%{query}%", limit]
        )
    return [dict(zip(res.columns, r)) for r in res.rows]


async def list_all_user_ids() -> list[int]:
    """Hammaga xabar yuborish uchun -- barcha (bloklanmagan) foydalanuvchi
    ID'lari."""
    c = get_client()
    res = await c.execute("SELECT telegram_id FROM users WHERE is_banned = 0")
    return [r[0] for r in res.rows]


async def list_users_page(offset: int, limit: int = 10) -> tuple[list[dict], int]:
    """Admin panel -- 'Foydalanuvchilar' ro'yxati uchun SAHIFALAB
    (oxirgi qo'shilgandan boshlab) chiqaradi. Qaytaradi:
    (shu sahifadagi foydalanuvchilar, JAMI foydalanuvchi soni)."""
    c = get_client()
    total = (await c.execute("SELECT COUNT(*) FROM users")).rows[0][0]
    res = await c.execute(
        "SELECT telegram_id, username, first_name, is_banned FROM users "
        "ORDER BY joined_at DESC, telegram_id DESC LIMIT ? OFFSET ?",
        [limit, offset],
    )
    rows = [dict(zip(res.columns, r)) for r in res.rows]
    return rows, total


# ════════════════════════════════════════════════════════════
#  GLOBAL SOZLAMALAR (limitlar)
# ════════════════════════════════════════════════════════════
async def get_settings() -> dict:
    c = get_client()
    res = await c.execute("SELECT * FROM settings WHERE id = 1")
    return dict(zip(res.columns, res.rows[0]))


async def set_global_limit(field: str, value: int) -> None:
    """`field`: 'default_create_limit' | 'default_read_limit' | 'max_images_per_pdf'.
    Faqat shu NOMDAN KEYIN qo'shiladigan yangi foydalanuvchilarga ta'sir
    qiladi (mavjudlarning shaxsiy limitiga tegmaydi) -- 'hammaga
    qo'llash' kerak bo'lsa alohida funksiya chaqiriladi."""
    c = get_client()
    await c.execute(f"UPDATE settings SET {field} = ? WHERE id = 1", [value])


async def apply_limit_to_all(kind: str, value: int) -> None:
    """Admin 'hammaga limitlar'dan kunlik limitni darhol BARCHA mavjud
    foydalanuvchilarga ham qo'llaganida chaqiriladi."""
    col = "daily_create_limit" if kind == "create" else "daily_read_limit"
    c = get_client()
    await c.execute(f"UPDATE users SET {col} = ?", [value])


# ════════════════════════════════════════════════════════════
#  MAJBURIY OBUNA KANALLARI
# ════════════════════════════════════════════════════════════
async def add_required_channel(channel_id: str, title: str) -> None:
    c = get_client()
    await c.execute(
        "INSERT INTO required_channels (channel_id, title, created_at) VALUES (?, ?, ?)",
        [channel_id, title, _now()],
    )


async def remove_required_channel(row_id: int) -> None:
    c = get_client()
    await c.execute("DELETE FROM required_channels WHERE id = ?", [row_id])
    await c.execute("DELETE FROM channel_joins WHERE channel_row_id = ?", [row_id])


async def list_active_required_channels() -> list[dict]:
    c = get_client()
    res = await c.execute(
        "SELECT * FROM required_channels WHERE is_active = 1 ORDER BY id ASC"
    )
    return [dict(zip(res.columns, r)) for r in res.rows]


async def has_joined_channel(channel_row_id: int, telegram_id: int) -> bool:
    c = get_client()
    res = await c.execute(
        "SELECT 1 FROM channel_joins WHERE channel_row_id = ? AND telegram_id = ?",
        [channel_row_id, telegram_id],
    )
    return len(res.rows) > 0


async def record_channel_join(channel_row_id: int, telegram_id: int) -> None:
    c = get_client()
    await c.execute(
        "INSERT INTO channel_joins (channel_row_id, telegram_id, joined_at) VALUES (?, ?, ?) "
        "ON CONFLICT(channel_row_id, telegram_id) DO NOTHING",
        [channel_row_id, telegram_id, _now()],
    )


async def count_channel_members(channel_row_id: int) -> int:
    """Admin panelda 'kanalga nechta odam shu bot orqali qo'shilgan'
    sonini ko'rsatish uchun -- haqiqiy Telegram a'zolik soni emas,
    balki botimiz orqali 'obuna bo'ldim' deb belgilangan odamlar soni."""
    c = get_client()
    res = await c.execute(
        "SELECT COUNT(*) FROM channel_joins WHERE channel_row_id = ?", [channel_row_id]
    )
    return res.rows[0][0]


# ════════════════════════════════════════════════════════════
#  XOTIRA (vaqtincha qolib ketgan fayllar) BOSHQARUVI
# ════════════════════════════════════════════════════════════
async def add_pending_file(telegram_id: int, file_path: str, size_bytes: int) -> int:
    c = get_client()
    res = await c.execute(
        "INSERT INTO pending_files (telegram_id, file_path, size_bytes, created_at) "
        "VALUES (?, ?, ?, ?)",
        [telegram_id, file_path, size_bytes, _now()],
        return_last_rowid=True,
    )
    return res.rows[0][0]


async def remove_pending_files_for_user(telegram_id: int) -> list[str]:
    """PDF yaratilgach yoki bekor qilingach chaqiriladi -- shu
    foydalanuvchiga tegishli barcha pending yozuvlarni o'chiradi va
    disk fayl yo'llarini qaytaradi (chaqiruvchi tomonda diskdan
    o'chiriladi)."""
    c = get_client()
    res = await c.execute(
        "SELECT file_path FROM pending_files WHERE telegram_id = ?", [telegram_id]
    )
    paths = [r[0] for r in res.rows]
    await c.execute("DELETE FROM pending_files WHERE telegram_id = ?", [telegram_id])
    return paths


async def list_pending_files_summary() -> list[dict]:
    """Admin panel 'Xotira' bo'limi -- kim nechta fayl, qancha hajm
    qoldirgani (foydalanuvchi bo'yicha guruhlangan)."""
    c = get_client()
    res = await c.execute("""
        SELECT telegram_id, COUNT(*) as file_count, COALESCE(SUM(size_bytes), 0) as total_size,
               MIN(created_at) as oldest
        FROM pending_files GROUP BY telegram_id ORDER BY oldest ASC
    """)
    return [dict(zip(res.columns, r)) for r in res.rows]


async def clear_all_pending_files() -> list[str]:
    c = get_client()
    res = await c.execute("SELECT file_path FROM pending_files")
    paths = [r[0] for r in res.rows]
    await c.execute("DELETE FROM pending_files")
    return paths


# ════════════════════════════════════════════════════════════
#  ADMIN <-> FOYDALANUVCHI XABAR BOG'LASH
# ════════════════════════════════════════════════════════════
async def link_admin_message(telegram_id: int, admin_msg_id: int) -> None:
    """Admin tomonga yuborilgan xabarning ID'si bilan foydalanuvchini
    bog'laydi -- admin shu xabarga REPLY qilganda, qaysi foydalanuvchiga
    javob yozishni bilish uchun."""
    c = get_client()
    await c.execute(
        "INSERT INTO admin_messages (telegram_id, admin_msg_id, created_at) VALUES (?, ?, ?)",
        [telegram_id, admin_msg_id, _now()],
    )


async def get_user_for_admin_message(admin_msg_id: int) -> int | None:
    c = get_client()
    res = await c.execute(
        "SELECT telegram_id FROM admin_messages WHERE admin_msg_id = ?", [admin_msg_id]
    )
    return res.rows[0][0] if res.rows else None
