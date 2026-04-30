"""
notification_i18n.py — Traductions backend pour les notifications.

Utilisation:
    from app.utils.notification_i18n import nt
    subject = f"[Sentinel] {nt('alert', lang)} {severity.upper()} : {rule_name}"

Pour ajouter une langue:
    1. Créer le fichier json correspondant (ex: static/i18n/es.json)
    2. Traduire toutes les clés dans la section "notifications"
    3. Le fallback est 'en' si la clé ou la langue n'existe pas
"""

import json
import os

def nt(key: str, lang: str = 'fr') -> str:
    """
    Retourne la traduction de la clé pour la langue donnée.
    Lit en direct depuis static/i18n/{lang}.json (permet l'ajout à chaud).
    Fallback: en → fr → clé brute.
    """
    def load_translations(language: str):
        filepath = os.path.join("static", "i18n", f"{language}.json")
        try:
            with open(filepath, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
                return data.get("notifications", {})
        except Exception:
            return {}

    translations = load_translations(lang)
    if key in translations:
        return translations[key]
    
    en = load_translations('en')
    if key in en:
        return en[key]
        
    fr = load_translations('fr')
    if key in fr:
        return fr[key]
        
    return key
