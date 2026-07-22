import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

# =====================================================
# DATABASE PATH
# =====================================================
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'data' / 'employee.db'

# Ensure data directory exists
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# =====================================================
# CONNECT
# =====================================================
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

try:
    # =====================================================
    # CREATE ADMINS TABLE
    # =====================================================
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

    # =====================================================
    # CREATE / UPDATE ADMIN USER
    # =====================================================
    NAME = 'Yash Kabure'
    EMAIL = 'admin@apexhr.com'
    PASSWORD = 'Yash@2005'
    password_hash = generate_password_hash(PASSWORD)

    # Insert admin if not exists
    cursor.execute('''
    INSERT OR IGNORE INTO admins (name, email, password_hash, role)
    VALUES (?, ?, ?, ?)
    ''', (
        NAME,
        EMAIL,
        password_hash,
        'HR Manager'
    ))

    # Always update password to latest value
    cursor.execute('''
    UPDATE admins
    SET password_hash = ?
    WHERE email = ?
    ''', (
        password_hash,
        EMAIL
    ))

    conn.commit()
    
    print('✅ Admin table ensured')
    print('✅ Admin account ensured')
    print('Email:', EMAIL)
    print('Password:', PASSWORD)

finally:
    conn.close()