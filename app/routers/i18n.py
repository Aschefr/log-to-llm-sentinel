import os
import json
from fastapi import APIRouter

router = APIRouter(prefix="/api/i18n", tags=["i18n"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
I18N_DIR = os.path.join(BASE_DIR, "static", "i18n")


@router.get("/languages")
def list_languages():
    """Scanne le dossier static/i18n/ et retourne les langues disponibles."""
    languages = []
    if not os.path.isdir(I18N_DIR):
        return languages
    for filename in sorted(os.listdir(I18N_DIR)):
        if filename.endswith(".json"):
            code = filename[:-5]
            filepath = os.path.join(I18N_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                meta = data.get("_meta", {})
                languages.append({
                    "code": code,
                    "name": meta.get("name", code.upper()),
                    "flag": meta.get("flag", "🌐"),
                })
            except Exception:
                languages.append({"code": code, "name": code.upper(), "flag": "🌐"})
    return languages
