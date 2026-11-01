# Guide de Publication d'une Release (BUILD_GUIDE)

Ce guide détaille les étapes et les vérifications indispensables pour publier une nouvelle version de **Log-to-LLM Sentinel**.

---

## 1. Vérifications Pré-release (Checklist de Sécurité & Nettoyage)

Avant de lancer la publication, effectuez les vérifications suivantes dans votre environnement local :

### 🔐 Données Sensibles & Identifiants
- [ ] **Pas de secrets dans le code :** Vérifiez qu'aucune clé d'API (Ollama, Discord, Apprise, etc.), jeton ou mot de passe n'a été codé en dur dans les fichiers source ou de configuration.
- [ ] **Fichier `.env` :** Assurez-vous que votre fichier `.env` local n'est pas indexé (il doit être listé dans `.gitignore`). Seul `.env.example` doit être partagé.

### 🧹 Nettoyage des Fichiers Temporaires & Bases de Données
- [ ] **Base de données locale (`sentinel.db`) :** Ne validez pas de base de données contenant des logs réels ou des configurations sensibles. Le fichier `sentinel.db` à la racine doit être propre ou ignoré si nécessaire.
- [ ] **Logs de test :** Supprimez les fichiers de logs générés dans `./logs/` ou `./data/syslog/` durant vos sessions de développement et de débogage.
- [ ] **Scripts scratch :** Supprimez ou nettoyez les scripts de test temporaires situés dans le dossier `scratch/`.

---

## 2. Mise à Jour des Documents de Suivi

Mettez à jour les fichiers de documentation principaux pour refléter les changements apportés :

- **`CHANGELOG.md`** : Ajoutez une section détaillant les nouveautés, améliorations et corrections de la version en cours de publication (suivez le format existant *Keep a Changelog*).
- **`DevTracker.yaml`** : Mettez à jour le statut des tâches accomplies (`status: DONE`), modifiez la date de dernière mise à jour globale (`updated: AAAA-MM-JJ`) et notez les éventuelles nouvelles règles de dev.
- **`README.md`** : Si la nouvelle version introduit des variables d'environnement, des volumes Docker ou des configurations inédites, documentez-les dans le README.
- **`version.txt`** : Ce fichier contient le numéro de version. Il est mis à jour **automatiquement** par le script de release, vous n'avez pas besoin de le modifier manuellement.

---

## 3. Script Automatique de Publication

Le projet contient un script PowerShell complet qui automatise le commit, le tag, la release GitHub et le build/push Docker : [scripts/publish_release.ps1](file:///d:/Code%20Projects/log-to-llm-sentinel/scripts/publish_release.ps1).

### Prérequis à la publication
Avant de lancer le script, assurez-vous d'être connecté à vos comptes sur votre terminal :
1. **GitHub CLI (`gh`)** : Vous devez être authentifié pour créer la release automatique.
   ```powershell
   gh auth login
   ```
2. **Docker Hub** : Vous devez être connecté pour pousser les images sur le dépôt `aschefr/log-to-llm-sentinel`.
   ```powershell
   docker login
   ```

### Lancement du script de release

Ouvrez une console PowerShell à la racine du projet et exécutez la commande suivante :

```powershell
# Pour tester le processus sans pousser ni builder sur Docker Hub (Dry Run)
.\scripts\publish_release.ps1 -DryRun

# Pour publier la release finale
.\scripts\publish_release.ps1
```

### Ce que fait le script automatiquement :
1. **Commit des modifications en cours** : Ajoute tous les fichiers modifiés et crée un commit de bump de version.
2. **Calcul de la version** : Génère automatiquement la version au format `1.[merges+2].[commits]` basé sur l'historique Git et met à jour [version.txt](file:///d:/Code%20Projects/log-to-llm-sentinel/version.txt).
3. **Tag Git & Push** : Crée un tag Git local (ex: `v1.2.298`) et le pousse sur le dépôt GitHub principal.
4. **Release GitHub** : Crée automatiquement une release GitHub officielle avec génération de notes de version via `gh release`.
5. **Build & Push Docker** : Compile l'image Docker localement avec les étiquettes `:latest` et `:<version>`, puis pousse ces images sur Docker Hub (`aschefr/log-to-llm-sentinel`).

---

## 4. Jeu de régression du prompt (si les prompts ou le modèle changent)

Le script `scripts/llm_regression.py` rejoue des cas de logs contre le modèle configuré et vérifie la sévérité obtenue :

```powershell
# Cas d'exemple anonymisés fournis avec le projet
python scripts/llm_regression.py scripts/llm_regression.example.json --url http://<ollama>:11434 --model <modele> --ctx 32768 --no-think

# Vos propres cas (lignes réelles) : à garder dans data/, qui n'est pas versionné
docker exec <conteneur> python scripts/llm_regression.py data/llm_regression.json
```

Le script se termine avec un code d'erreur si un cas n'est pas conforme (utile avant une release qui modifie `app/services/prompts.py`).
