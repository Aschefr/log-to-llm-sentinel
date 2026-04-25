import urllib.request
import json
import time

url = "http://192.168.20.125:11434/api/chat"
payload = {
    "model": "qwen3.5:0.8b",
    "messages": [
        {
            "role": "user",
            "content": r"""Analyse de contenu de cette erreur qui a été logé, explique pourquoi c'est arrivé et quelles étapes réaliser pour corriger le problème. S'il s'agit juste d'une alerte, alors aucune action n'est à entreprendre. Inclus un résumé court à la fin. Réponds directement. Pas de préambule, pas de raisonnement interne, sois le plus concis possible.

Analyse la ligne de log suivante et détermine sa sévérité.
Ta réponse DOIT impérativement commencer par une ligne indiquant la sévérité sous ce format EXACT :
SEVERITY: [info|warning|critical]

Ensuite, fournis un résumé court et explicatif de l'incident.

Contexte de l'application: Journal de l'application Nextcloud installé en docker AIO sur un mini pc GMKtec Ryzen 7730U, 32Gb de ram, 4To SSD.
Ligne déclenchante: [updater] \OC\Updater::startCheckCodeIntegrity: Starting code integrity check..."""
        }
    ],
    "stream": True,
    "options": {
        "num_ctx": 4096
    }
}

req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), method="POST")
req.add_header('Content-Type', 'application/json')

start = time.time()
try:
    with urllib.request.urlopen(req) as response:
        print("Connected...", flush=True)
        think_count = 0
        resp_count = 0
        for i, line in enumerate(response):
            chunk = json.loads(line.decode('utf-8').strip())
            
            message = chunk.get("message", {})
            if chunk.get("thinking") or message.get("thinking"):
                think_count += 1
            if message.get("content"):
                resp_count += 1
            if (think_count + resp_count) % 50 == 0:
                print(f"{time.time()-start:.1f}s | Think chunks: {think_count}, Response chunks: {resp_count}", flush=True)
        print(f"Total time: {time.time()-start:.1f}s | Think chunks: {think_count}, Response chunks: {resp_count}", flush=True)
except Exception as e:
    print("Error:", e, flush=True)
