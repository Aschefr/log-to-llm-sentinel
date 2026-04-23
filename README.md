# log-to-llm-sentinel

Petit projet Python (FastAPI) pour lancer une API web localement.

Ce README est pensé pour **les personnes débutantes** : si tu suis les étapes dans l’ordre, tu devrais arriver à démarrer l’app.

## C’est quoi ?

- Une **API** faite avec **FastAPI**
- Lancement via **Uvicorn**
- Dépendances Python listées dans `requirements.txt`

## Pré-requis

- **Python 3.10+** (idéalement 3.11 ou 3.12)
- **Git** (optionnel, mais pratique)

Vérifier que Python est bien installé :

```bash
python --version
```

## Installation (Windows)

### 1) Récupérer le projet

Si tu as déjà le dossier, passe à l’étape suivante. Sinon :

```bash
git clone <URL_DU_DEPOT>
cd log-to-llm-sentinel
```

### 2) Créer un environnement virtuel (recommandé)

```bash
python -m venv .venv
```

Activer l’environnement :

```bash
.venv\Scripts\activate
```

Tu devrais voir `(.venv)` au début de ta ligne de commande.

### 3) Installer les dépendances

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Démarrer l’application

> Selon le projet, le point d’entrée peut varier. Les commandes ci-dessous sont les plus courantes.

Essaie d’abord :

```bash
uvicorn main:app --reload
```

Si ça ne marche pas, essaie aussi :

```bash
uvicorn app.main:app --reload
```

Ensuite ouvre :

- **Docs interactives (Swagger)** : `http://127.0.0.1:8000/docs`
- **Redoc** : `http://127.0.0.1:8000/redoc`

## Structure (à compléter)

Si tu ne sais pas où est `app`, cherche un fichier qui contient quelque chose comme :

- `app = FastAPI()`

Souvent c’est dans `main.py` ou `app/main.py`.

## Problèmes courants (FAQ)

### “uvicorn n’est pas reconnu…”

Tu n’es probablement pas dans l’environnement virtuel.

1) Active `.venv` :

```bash
.venv\Scripts\activate
```

2) Réessaie :

```bash
uvicorn main:app --reload
```

### “ModuleNotFoundError: No module named 'main'”

Ça veut dire que `main.py` n’est pas à la racine, ou que le module a un autre nom.

- Cherche où se trouve l’objet `app = FastAPI()`
- Puis adapte la commande :
  - Exemple si le fichier est `app/main.py` : `uvicorn app.main:app --reload`

### “Address already in use”

Le port 8000 est déjà utilisé. Tu peux changer de port :

```bash
uvicorn main:app --reload --port 8001
```

## Besoin d’aide ?

Si tu bloques, copie/colle :

- la commande que tu as lancée
- le message d’erreur complet
- ton fichier d’entrée (celui qui contient `FastAPI()`)

et je t’aiderai à corriger.
