
import sqlite3
from werkzeug.security import check_password_hash







import sqlite3
from pathlib import Path
from werkzeug.security import check_password_hash

# =====================================================
# PRODUCTION DATABASE PATH
# =====================================================

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / 'data' / 'employee.db'


def authenticate_admin(email, password):
    """Verify admin credentials."""

    print("\\n========== AUTH DEBUG ==========")
    print("Database path:", DB_PATH)
    print("Input email:", email)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        '''
        SELECT id, name, email, password_hash, role
        FROM admins
        WHERE email = ?
        ''',
        (email,)
    )

    admin = cursor.fetchone()
    conn.close()

    print("DB result:", admin)

    if not admin:
        print("❌ Admin not found")
        return None

    admin_id, name, email, password_hash, role = admin

    is_valid = check_password_hash(password_hash, password)

    print("Password valid:", is_valid)
    print("================================\\n")

    if is_valid:
        return {
            'id': admin_id,
            'name': name,
            'email': email,
            'role': role
        }

    return None

