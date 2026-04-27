import requests
import time
import random
import sys

def main():
    if len(sys.argv) < 2:
        print("Usage: python simulate_logs.py <webhook_url>")
        print("Exemple: python simulate_logs.py http://localhost:10911/api/webhook/logs/1")
        sys.exit(1)

    url = sys.argv[1]
    
    # Exemples de logs réalistes
    log_templates = [
        "[INFO] Connexion réussie pour l'utilisateur admin",
        "[WARNING] Temps de réponse élevé sur /api/users (800ms)",
        "[ERROR] Unhandled exception: Division by zero",
        "[INFO] Tâche planifiée 'backup' démarrée",
        "[CRITICAL] Database connection timeout: connection refused",
        "[INFO] Déconnexion de l'utilisateur john_doe",
        "[ERROR] Permission denied to access file /etc/shadow"
    ]

    print(f"Simulation de logs vers: {url}")
    print("Appuyez sur Ctrl+C pour arrêter.")
    
    try:
        while True:
            # Générer 1 à 3 lignes de log
            num_lines = random.randint(1, 3)
            lines = [random.choice(log_templates) for _ in range(num_lines)]
            
            payload = {"lines": lines}
            
            try:
                response = requests.post(url, json=payload, timeout=5)
                if response.status_code == 200:
                    for line in lines:
                        print(f"-> Envoyé: {line}")
                else:
                    print(f"[!] Erreur serveur {response.status_code}: {response.text}")
            except Exception as e:
                print(f"[!] Erreur de connexion: {e}")
                
            # Attendre entre 2 et 5 secondes
            time.sleep(random.uniform(2, 5))
            
    except KeyboardInterrupt:
        print("\nArrêt de la simulation.")

if __name__ == "__main__":
    main()
