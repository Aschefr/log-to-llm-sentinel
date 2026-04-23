import json
import urllib.request
import urllib.error
from typing import Optional


class OllamaService:
    """
    Service de communication avec Ollama pour l'analyse des logs.
    """

    def analyze(
        self,
        prompt: str,
        url: str = "http://host.docker.internal:11434",
        model: str = "llama3",
        timeout: int = 30,
    ) -> str:
        """
        Envoie un prompt à Ollama et retourne la réponse.
        """
        api_url = f"{url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                api_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result.get("response", "Aucune réponse d'Ollama")

        except urllib.error.URLError as e:
            return f"[Erreur Ollama] Impossible de joindre Ollama: {str(e)}"
        except json.JSONDecodeError:
            return "[Erreur Ollama] Réponse JSON invalide"
        except Exception as e:
            return f"[Erreur Ollama] {str(e)}"
