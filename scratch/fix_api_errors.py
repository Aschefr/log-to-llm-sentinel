import os
import re
import json
import glob

MAPPING = {
    "Orchestrateur non configuré": "orchestrator_not_configured",
    "Règle non trouvée": "rule_not_found",
    "Règle désactivée": "rule_disabled",
    "JSON invalide": "invalid_json",
    "Client Closed Request": "client_closed_request",
    "Orchestrateur non initialisé": "orchestrator_not_initialized",
    "Analyse non trouvée": "analysis_not_found",
    "Configuration globale non trouvée": "global_config_not_found",
    "Configuration globale introuvable": "global_config_not_found",
    "Configuration non trouvée": "config_not_found",
    "Config non trouvée": "config_not_found",
    "Tâche non trouvée": "task_not_found",
    "Données manquantes": "missing_data",
    "Question manquante": "missing_question",
    "Résultat introuvable": "result_not_found",
    "Session introuvable": "session_not_found",
    "period_end must be after period_start": "invalid_period",
    "granularity_s must be >= 60": "invalid_granularity",
    "Aucun fichier valide fourni.": "no_valid_file",
    "Fichier introuvable sur le disque.": "file_not_found",
    "Le chemin doit être absolu": "path_must_be_absolute"
}

EN_TRANSLATIONS = {
    "orchestrator_not_configured": "Orchestrator not configured",
    "rule_not_found": "Rule not found",
    "rule_disabled": "Rule disabled",
    "invalid_json": "Invalid JSON",
    "client_closed_request": "Client closed request",
    "orchestrator_not_initialized": "Orchestrator not initialized",
    "analysis_not_found": "Analysis not found",
    "global_config_not_found": "Global configuration not found",
    "config_not_found": "Configuration not found",
    "task_not_found": "Task not found",
    "missing_data": "Missing data",
    "missing_question": "Missing question",
    "result_not_found": "Result not found",
    "session_not_found": "Session not found",
    "invalid_period": "End date must be after start date",
    "invalid_granularity": "Granularity must be >= 60s",
    "no_valid_file": "No valid file provided.",
    "file_not_found": "File not found on disk.",
    "path_must_be_absolute": "Path must be absolute",
    "error_unknown": "Unknown error",
    "error_api": "API error"
}

FR_TRANSLATIONS = {
    "orchestrator_not_configured": "Orchestrateur non configuré",
    "rule_not_found": "Règle non trouvée",
    "rule_disabled": "Règle désactivée",
    "invalid_json": "JSON invalide",
    "client_closed_request": "Requête annulée par le client",
    "orchestrator_not_initialized": "Orchestrateur non initialisé",
    "analysis_not_found": "Analyse non trouvée",
    "global_config_not_found": "Configuration globale introuvable",
    "config_not_found": "Configuration introuvable",
    "task_not_found": "Tâche introuvable",
    "missing_data": "Données manquantes",
    "missing_question": "Question manquante",
    "result_not_found": "Résultat introuvable",
    "session_not_found": "Session introuvable",
    "invalid_period": "La date de fin doit être après la date de début",
    "invalid_granularity": "La granularité doit être >= 60s",
    "no_valid_file": "Aucun fichier valide fourni.",
    "file_not_found": "Fichier introuvable sur le disque.",
    "path_must_be_absolute": "Le chemin doit être absolu",
    "error_unknown": "Erreur inconnue",
    "error_api": "Erreur API"
}

def update_routers():
    routers_dir = "app/routers"
    for py_file in glob.glob(os.path.join(routers_dir, "*.py")):
        with open(py_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        changed = False
        for old_str, new_key in MAPPING.items():
            pattern1 = f'detail="{old_str}"'
            pattern2 = f"detail='{old_str}'"
            if pattern1 in content:
                content = content.replace(pattern1, f'detail="{new_key}"')
                changed = True
            if pattern2 in content:
                content = content.replace(pattern2, f'detail="{new_key}"')
                changed = True
                
        if changed:
            with open(py_file, "w", encoding="utf-8") as f:
                f.write(content)

def update_i18n():
    for lang, trans in [("fr", FR_TRANSLATIONS), ("en", EN_TRANSLATIONS)]:
        filepath = f"static/i18n/{lang}.json"
        with open(filepath, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
            
        data["api_errors"] = trans
        
        if "chat" not in data:
            data["chat"] = {}
            
        if lang == "fr":
            data["chat"]["label_user"] = "Vous"
            data["chat"]["label_ollama"] = "Ollama"
            data["chat"]["generation_cancelled"] = "Génération annulée."
            data["chat"]["error_create_conv"] = "Erreur lors de la création de la conversation :"
        else:
            data["chat"]["label_user"] = "You"
            data["chat"]["label_ollama"] = "Ollama"
            data["chat"]["generation_cancelled"] = "Generation cancelled."
            data["chat"]["error_create_conv"] = "Error creating conversation:"

        with open(filepath, "w", encoding="utf-8-sig") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.write("\n")

if __name__ == "__main__":
    update_routers()
    update_i18n()
    print("Done")
