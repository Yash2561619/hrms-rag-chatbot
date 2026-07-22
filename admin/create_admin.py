# import sqlite3
# from werkzeug.security import generate_password_hash

# # ===== CHANGE THESE VALUES =====
# name = 'Yash Kabure'
# email = 'admin@apexhr.com'
# password = 'Admin@123'   # You can change this
# # ===============================

# conn = sqlite3.connect('data/employee.db')
# cursor = conn.cursor()

# # Generate secure hash
# password_hash = generate_password_hash(password)

# try:
#     cursor.execute('''
#     INSERT INTO admins (name, email, password_hash)
#     VALUES (?, ?, ?)
#     ''', (name, email, password_hash))

#     conn.commit()
#     print(' Admin account created successfully!')
#     print(f' Email: {email}')
#     print(f' Password: {password}')

# except sqlite3.IntegrityError:
#     print('Admin with this email already exists.')

# finally:
#     conn.close()





# # Name: Yash Kabure
# # Email: admin@apexhr.com
# # Hash starts with: scrypt:32768:8:1$vJ0
# Password: Admin@123

# import sqlite3
# from werkzeug.security import generate_password_hash

# # CHANGE THESE
# new_email = 'yash@apexhr.com'
# new_password = 'Yash@2026'

# conn = sqlite3.connect('hrms.db')
# cursor = conn.cursor()

# new_hash = generate_password_hash(new_password)

# cursor.execute('''
# UPDATE admins
# SET email = ?, password_hash = ?
# WHERE id = 1
# ''', (new_email, new_hash))

# conn.commit()
# conn.close()

# print('✅ Admin credentials updated!')
# print('Email:', new_email)
# print('Password:', new_password)


# import sqlite3
# from werkzeug.security import generate_password_hash, check_password_hash

# EMAIL = 'admin@apexhr.com'
# NEW_PASSWORD = 'Yash@2005'

# conn = sqlite3.connect('data/employee.db')
# cursor = conn.cursor()

# # Generate new hash
# new_hash = generate_password_hash(NEW_PASSWORD)

# # Update password
# cursor.execute(
#     '''
#     UPDATE admins
#     SET password_hash = ?
#     WHERE email = ?
#     ''',
#     (new_hash, EMAIL)
# )

# conn.commit()

# print('Rows updated:', cursor.rowcount)

# # Verify immediately from database
# cursor.execute(
#     'SELECT password_hash FROM admins WHERE email = ?',
#     (EMAIL,)
# )

# row = cursor.fetchone()

# if row:
#     stored_hash = row[0]

#     print('Hash starts with:', stored_hash[:30])

#     # CRITICAL TEST
#     result = check_password_hash(stored_hash, NEW_PASSWORD)

#     print('Password verification result:', result)

#     if result:
#         print('✅ Password reset and verification successful!')
#     else:
#         print('❌ Verification failed!')
# else:
#     print('❌ Admin not found.')

# conn.close()

import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'data' / 'employee.db'

EMAIL = 'admin@apexhr.com'
NEW_PASSWORD = 'Yash@2005'

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

new_hash = generate_password_hash(NEW_PASSWORD)

cursor.execute(
    '''
    UPDATE admins
    SET password_hash = ?
    WHERE email = ?
    ''',
    (new_hash, EMAIL)
)

conn.commit()

print('Rows updated:', cursor.rowcount)
print('✅ Password reset successful!')

conn.close()