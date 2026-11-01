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
- **`version.txt`** : Contient la version courante au format [Semantic Versioning](https://semver.org/lang/fr/) (`MAJEUR.MINEUR.CORRECTIF`). Il est versionné et mis à jour **par le script de release** : ne le modifiez pas à la main.
- **`CHANGELOG.md`** : Rédigez les changements sous `## [Unreleased]` au fil de l'eau. Le script transforme cette section en `## [X.Y.Z] - AAAA-MM-JJ` et l'utilise comme notes de la release GitHub.

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

### Choisir le type de version (SemVer)

| Option | Quand l'utiliser | Exemple |
|---|---|---|
| `-Bump patch` (défaut) | Corrections de bugs uniquement | 1.3.0 → 1.3.1 |
| `-Bump minor` | Nouvelles fonctionnalités compatibles (migrations de BDD automatiques comprises) | 1.3.1 → 1.4.0 |
| `-Bump major` | Changement incompatible : configuration à refaire, volume ou variable d'environnement modifiés, API cassée | 1.4.0 → 2.0.0 |
| `-Version X.Y.Z` | Version explicite (cas particuliers) | |

### Lancement du script de release

Le script se lance **depuis `main`** (intégrez d'abord votre branche), avec une branche à jour par rapport à `origin/main` :

```powershell
# Aperçu : version calculée et notes de release, sans rien modifier
.\scripts\publish_release.ps1 -Bump minor -DryRun

# Publication
.\scripts\publish_release.ps1 -Bump minor
```

### Ce que fait le script automatiquement :
1. **Vérifications** : branche `main`, à jour avec `origin/main`, `version.txt` valide, tag inexistant, section `## [Unreleased]` non vide dans le CHANGELOG.
2. **Version** : calcule la nouvelle version à partir de `version.txt` et de l'option choisie, met à jour `version.txt` et date la section du CHANGELOG.
3. **Commit, tag & push** : commit `release: vX.Y.Z` (incluant les modifications en cours), tag annoté `vX.Y.Z`, push de `main` et du tag.
4. **Release GitHub** : `gh release create` avec la section du CHANGELOG comme notes.
5. **Build & Push Docker** : image `aschefr/log-to-llm-sentinel` étiquetée `:X.Y.Z` et `:latest`.

Le script s'arrête à la première commande en échec. L'application compare sa version à la **dernière release GitHub** pour signaler une mise à jour disponible.

> Historique : jusqu'à la v1.2.303, la version était calculée à partir du nombre de commits (`1.[merges+2].[commits]`). La numérotation SemVer commence à la v1.3.0.

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
