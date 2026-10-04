import json
import os

_LOCALES: dict[str, dict] = {}
_DIR = os.path.join(os.path.dirname(__file__), "locales")


def load_locales() -> None:
    for fname in os.listdir(_DIR):
        if fname.endswith(".json"):
            lang = fname.replace(".json", "")
            with open(os.path.join(_DIR, fname), encoding="utf-8") as f:
                _LOCALES[lang] = json.load(f)


def t(lang: str, key: str, **kwargs) -> str:
    """Tarjima qaytaradi. Til yoki kalit topilmasa -- 'uz'ga, keyin
    kalitning o'ziga qaytiladi (bot hech qachon xato bilan to'xtab
    qolmasligi uchun)."""
    data = _LOCALES.get(lang) or _LOCALES.get("uz") or {}
    text = data.get(key)
    if text is None:
        text = (_LOCALES.get("uz") or {}).get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
