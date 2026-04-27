"""
Test webhook: create a webhook rule, send simulated logs, check response.
"""
import requests
import json
import uuid
import time
import random

BASE = "http://localhost:10911"
TOKEN = str(uuid.uuid4())
WEBHOOK_PATH = f"[WEBHOOK]:{TOKEN}"
WEBHOOK_URL = f"{BASE}/api/webhook/logs/{TOKEN}"

# 1. Create webhook rule
print(f"[1] Creating webhook rule (token={TOKEN[:8]}...)")
rule_data = {
    "name": "Test Webhook Simule",
    "log_file_path": WEBHOOK_PATH,
    "keywords": ["error", "exception", "critical", "timeout"],
    "application_context": "Test de webhook automatise",
    "enabled": True,
    "notify_on_match": False,
    "context_lines": 3,
    "anti_spam_delay": 10,
    "notify_severity_threshold": "info"
}
resp = requests.post(f"{BASE}/api/rules", json=rule_data, timeout=5)
if resp.status_code != 200:
    print(f"[!] Failed to create rule: {resp.status_code} {resp.text}")
    exit(1)

rule_id = resp.json().get("id")
print(f"[+] Rule created: id={rule_id}")
print(f"[+] Webhook URL: {WEBHOOK_URL}")

# 2. Send simulated log lines
log_lines = [
    "[INFO] App demarree avec succes",
    "[WARNING] Temps de reponse eleve (1200ms)",
    "[ERROR] Exception non geree: NullPointerException dans UserService.java:42",
    "[INFO] Reconnexion a la base de donnees reussie",
    "[CRITICAL] Database connection timeout apres 30s - connection refused",
    "[ERROR] Permission denied: /etc/shadow",
    "[INFO] Job planifie 'cleanup' termine",
]

print(f"\n[2] Sending {len(log_lines)} log lines to webhook...")
for i, line in enumerate(log_lines):
    payload = {"lines": [line]}
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=5)
        status = "OK" if r.status_code == 200 else f"ERR {r.status_code}"
        print(f"    [{status}] {line[:70]}")
    except Exception as e:
        print(f"    [FAIL] {e}")
    time.sleep(0.5)

# 3. Send a batch
print("\n[3] Sending batch of 3 lines...")
payload = {"lines": log_lines[2:5]}
r = requests.post(WEBHOOK_URL, json=payload, timeout=5)
print(f"    [{r.status_code}] lines_received={r.json().get('lines_received') if r.ok else r.text}")

# 4. Verify rule still exists
print("\n[4] Verifying rule in API...")
r = requests.get(f"{BASE}/api/rules/{rule_id}", timeout=5)
rule = r.json()
print(f"    name={rule['name']} path={rule['log_file_path'][:30]}...")

# 5. Cleanup
print(f"\n[5] Deleting test rule {rule_id}...")
r = requests.delete(f"{BASE}/api/rules/{rule_id}", timeout=5)
print(f"    {r.status_code} {r.json().get('message','')}")

print("\n=== Webhook test PASSED ===")
