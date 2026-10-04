# PDF Bot — Rasm ↔ PDF konvertatsiya boti

## Imkoniyatlar
- Rasm(lar) yuborilsa → PDF qilib beradi (nom so'raydi, 30 soniya javobsiz qolsa avtomatik)
- PDF yuborilsa → har sahifasini rasm qilib albumga yig'ib qaytaradi
- Admin bilan bog'lanish (ikki tomonlama xabar almashish)
- To'liq admin panel: statistika, foydalanuvchi qidirish/boshqarish, top ro'yxatlar,
  7-kunlik grafik, global limitlar, broadcast, majburiy obuna kanallari, xotira tozalash
- O'zbek va ingliz tillari
- Turso (libSQL) bazasi

## O'rnatish (Railway)

1. Repo'ni Railway'ga ulang (yoki shu papkani push qiling).
2. Railway "Variables" bo'limida `.env.example` dagi barcha o'zgaruvchilarni to'ldiring:
   - `API_ID`, `API_HASH` — https://my.telegram.org
   - `BOT_TOKEN` — @BotFather
   - `ADMIN_IDS` — sizning Telegram ID'ingiz (bir nechta bo'lsa vergul bilan)
   - `ADMIN_USERNAME` — @ belgisisiz
   - `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` — https://turso.tech dashboard'idan
3. Start command: `python main.py`
4. Deploy qiling.

## Lokal test

```bash
pip install -r requirements.txt
cp .env.example .env   # va qiymatlarni to'ldiring
python main.py
```

## Fayllar tuzilishi

- `main.py` — kirish nuqtasi
- `config.py` — muhit o'zgaruvchilari
- `database.py` — Turso/libSQL bilan ishlash (barcha SQL so'rovlar)
- `handlers.py` — foydalanuvchi oqimi (start, til, majburiy obuna, rasm/PDF)
- `admin_panel.py` — admin panel
- `pdf_utils.py` — rasm↔PDF konvertatsiya (img2pdf, PyMuPDF)
- `keyboards.py` — barcha tugmalar
- `i18n.py` + `locales/*.json` — tarjimalar

## Majburiy obuna kanali qo'shish

Admin panel → 📢 Majburiy obuna → ➕ Kanal qo'shish:
- **Haqiqiy Telegram kanal**: `@kanal_username` yoki `https://t.me/kanal_username` yuboring
  (bot shu kanalda ADMIN bo'lishi kerak, aks holda a'zolikni tekshira olmaydi)
- **Tashqi havola** (Instagram va h.k.): `Nom | https://havola` formatida, yoki shunchaki
  havolani yuboring (nom avtomatik olinadi). Bu HECH QACHON tekshirilmaydi va botni
  bloklamaydi — faqat ro'yxatda har doim (haqiqiy kanal bloklab turgan paytda) yoki
  bir martalik (hech narsa bloklamasa) ko'rsatiladi.
