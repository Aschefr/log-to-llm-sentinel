import os

# Change all notification-related logger tags to "Notification"
files_and_changes = {
    'app/services/notification_service.py': [
        ('"NotificationService"', '"Notification"'),
    ],
    'app/services/orchestrator.py': [
        # Only notification-related lines in trigger_notification
        ('logger.debug("Orchestrator", f"Envoi notification', 'logger.debug("Notification", f"Envoi notification'),
        ('logger.debug("Orchestrator", f"Notification lang=', 'logger.debug("Notification", f"Notification lang='),
        ('logger.debug("Orchestrator", f"Analyse trop longue', 'logger.debug("Notification", f"Analyse trop longue'),
    ],
}

for filepath, replacements in files_and_changes.items():
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for old, new in replacements:
        count = content.count(old)
        content = content.replace(old, new)
        print(f"  {filepath}: '{old[:50]}...' -> {count} replacement(s)")
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

print("\nAll notification logs now use tag 'Notification'")
