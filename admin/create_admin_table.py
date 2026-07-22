import sqlite3
import os

from pathlib import Path




BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'data' / 'employee.db'
os.makedirs(BASE_DIR / 'data', exist_ok=True)

conn = sqlite3.connect('DB_PATH')
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'HR Manager',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

conn.commit()
conn.close()

print('✅ Admins table created successfully!')