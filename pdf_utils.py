# ════════════════════════════════════════════════════════════
#  PDF <-> RASM KONVERTATSIYA
#
#  Rasm -> PDF:  img2pdf  (sifat yo'qotmaydi, juda tez, sof Python,
#                tashqi binary kerak emas)
#  PDF -> rasm:  PyMuPDF (fitz) (tashqi binary -- poppler -- kerak
#                emas, C kutubxonasi ustida ishlaydi, juda tez va
#                ko'p so'rov kelganda osilib qolmaydi)
# ════════════════════════════════════════════════════════════
import os
import img2pdf
import fitz  # PyMuPDF
from PIL import Image

import config


def images_to_pdf(image_paths: list[str], output_path: str) -> None:
    """Rasm fayllari ro'yxatini bitta PDF ga aylantiradi. RGBA/boshqa
    rejimdagi rasmlarni (masalan PNG shaffoflik bilan) img2pdf xato
    bermasligi uchun avval RGB'ga aylantirib, vaqtinchalik JPEG sifatida
    saqlab qo'yamiz -- bu eng ishonchli yo'l (img2pdf ba'zi PNG
    rejimlarida xato berishi mumkin)."""
    normalized = []
    for p in image_paths:
        try:
            img = Image.open(p)
            if img.mode != "RGB":
                img = img.convert("RGB")
                norm_path = p + ".norm.jpg"
                img.save(norm_path, "JPEG", quality=92)
                normalized.append(norm_path)
            else:
                normalized.append(p)
        except Exception:
            normalized.append(p)

    with open(output_path, "wb") as f:
        f.write(img2pdf.convert(normalized))

    # Vaqtinchalik normalizatsiya fayllarini tozalaymiz
    for p in normalized:
        if p.endswith(".norm.jpg") and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


def pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 150) -> list[str]:
    """PDF faylning har bir sahifasini alohida JPEG rasmga aylantiradi,
    `output_dir` ichiga saqlaydi va fayl yo'llari ro'yxatini qaytaradi.
    Shikastlangan/himoyalangan PDF bo'lsa, fitz xato tashlaydi --
    chaqiruvchi tomonda try/except bilan tutilishi kerak."""
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    doc = fitz.open(pdf_path)
    try:
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix)
            out_path = os.path.join(output_dir, f"page_{i + 1:03d}.jpg")
            pix.save(out_path)
            paths.append(out_path)
    finally:
        doc.close()
    return paths


def get_file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
