# ════════════════════════════════════════════════════════════
#  KONFIGURATSIYA -- hammasi muhit o'zgaruvchilaridan (Railway
#  "Variables" bo'limidan) o'qiladi. Local test uchun .env fayl
#  ham ishlatilishi mumkin (python-dotenv orqali).
# ════════════════════════════════════════════════════════════
import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Bitta yoki bir nechta admin (vergul bilan ajratilgan Telegram ID'lar)
ADMIN_IDS = [
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
]

# Turso (libSQL) ulanish ma'lumotlari
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

# Botning o'z username'i -- har bir qaytariladigan PDF/rasm albumining
# oxirida reklama sifatida ko'rsatiladi (@ belgisisiz saqlanadi, kerak
# bo'lganda qo'shiladi).
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").lstrip("@")

# ── Jarayon vaqt chegaralari (soniyalarda) ──────────────────────────
# Rasm to'plamiga navbatdagi rasm 30 soniya ichida kelmasa -- avtomatik
# PDF qilinadi (foydalanuvchi "Tayyor" bosmagan bo'lsa ham).
COLLECT_IDLE_TIMEOUT = 30
# "Tayyor" bosilgach, PDF nomi so'raladi -- 30 soniya ichida javob
# kelmasa, Telegram ismi bilan avtomatik PDF qilinadi.
FILENAME_WAIT_TIMEOUT = 30

# Vaqtinchalik fayllar papkasi (rasm/PDF yuklab olish, konvertatsiya
# uchun) -- har doim bot ishga tushganda tozalanadi.
TEMP_DIR = os.environ.get("TEMP_DIR", "/tmp/pdfbot_tmp")
os.makedirs(TEMP_DIR, exist_ok=True)

DEFAULT_LANGUAGE = "uz"

# ── PREMIUM -- boshlang'ich (standart) qiymatlar ────────────────────
# Bular faqat baza BIRINCHI marta yaratilganda ishlatiladi; keyin hammasi
# admin paneldan (🌟 Premium / 💳 To'lov sozlamalari) o'zgartiriladi.
DEFAULT_PREMIUM_DAYS = 30      # 0 = cheksiz muddat
DEFAULT_PRICE_UZS = 25000      # so'm (bank karta orqali to'lov)
DEFAULT_PRICE_USDT = 2         # USDT (kripto orqali to'lov)

# Chek yuborish bosqichi (foydalanuvchi "To'ladim" bosgach) shuncha soniya
# kutadi -- undan keyin foydalanuvchi yuborgan rasm/PDF chek deb EMAS,
# oddiy fayl deb qabul qilinadi (rasm->PDF jarayoni buzilmasligi uchun).
RECEIPT_WAIT_TIMEOUT = 900     # 15 daqiqa
