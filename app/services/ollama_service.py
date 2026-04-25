import json
import urllib.request
import urllib.error
import time
from typing import Optional

from app import logger


class OllamaService:
    """
    Service de communication avec Ollama pour l'analyse des logs.
    """

    def analyze(
        self,
        prompt: str,
        url: str = "http://ollama:11434",
        model: str = "llama3",
        timeout: int = 180,
        retries: int = 2,
        retry_delay_s: float = 2.0,
    ) -> str:
        """
        Envoie un prompt à Ollama et retourne la réponse.
        """
        base = (url or "http://ollama:11434").strip()
        # Users often paste "...:11434/api" in the UI. Normalize to the base.
        base = base.rstrip("/")
        if base.endswith("/api"):
            base = base[: -len("/api")]
        # Allow passing the full endpoint too.
        if base.endswith("/api/generate"):
            api_url = base
        else:
            api_url = f"{base}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }

        attempts = max(1, int(retries) + 1)
        last_err: Optional[str] = None

        logger.debug("OllamaService", f"Appel à {api_url} | modèle={model} | prompt={prompt[:80]}...")

        for attempt in range(1, attempts + 1):
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
                    answer = result.get("response", "Aucune réponse d'Ollama")
                    logger.debug("OllamaService", f"Réponse reçue ({len(answer)} car.) : {answer[:120]}")
                    return answer

            except urllib.error.HTTPError as e:
                # HTTPError is also a file-like object that may contain a JSON body.
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    body = ""

                try:
                    parsed = json.loads(body) if body else {}
                except Exception:
                    parsed = {}

                detail = (
                    parsed.get("error")
                    or parsed.get("detail")
                    or body.strip()
                    or e.reason
                    or "Erreur HTTP"
                )

                # Ollama can return 500 while loading a model; retry helps.
                retryable = (
                    getattr(e, "code", None) in (500, 503)
                    and "loading model" in str(detail).lower()
                )
                last_err = f"[Erreur Ollama] HTTP {getattr(e, 'code', '?')}: {detail}"
                logger.error("OllamaService", last_err)
                if retryable and attempt < attempts:
                    time.sleep(retry_delay_s * attempt)
                    continue
                return last_err

            except urllib.error.URLError as e:
                msg = str(e)
                last_err = f"[Erreur Ollama] Impossible de joindre Ollama: {msg}"
                logger.error("OllamaService", last_err)
                retryable = "timed out" in msg.lower() or "timeout" in msg.lower()
                if retryable and attempt < attempts:
                    time.sleep(retry_delay_s * attempt)
                    continue
                return last_err

            except json.JSONDecodeError:
                last_err = "[Erreur Ollama] Réponse JSON invalide"
                return last_err
            except Exception as e:
                last_err = f"[Erreur Ollama] {str(e)}"
                return last_err

        return last_err or "[Erreur Ollama] Erreur inconnue"
