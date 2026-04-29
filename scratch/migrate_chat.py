import sqlite3
import sys

try:
    db = sqlite3.connect('data/sentinel.db')
    try:
        db.execute('ALTER TABLE global_config ADD COLUMN chat_system_prompt TEXT DEFAULT ""')
    except Exception as e:
        print(f"Error adding chat_system_prompt: {e}")
        
    try:
        db.execute('ALTER TABLE global_config ADD COLUMN chat_lang VARCHAR DEFAULT ""')
    except Exception as e:
        print(f"Error adding chat_lang: {e}")
        
    db.commit()
    print("Migration finished")
except Exception as e:
    print(f"Failed to connect: {e}")
finally:
    db.close()
