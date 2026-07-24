# from database import get_connection

# conn = get_connection()
# cursor = conn.cursor()

# cursor.execute("""
# SELECT id, leave_type, leave_days, status, from_date, to_date
# FROM leave_requests
# WHERE employee_id = 'EMP001'
# ORDER BY id
# """)

# rows = cursor.fetchall()

# for row in rows:
#     print(row)

# conn.close()


# import sqlite3


# conn = sqlite3.connect('employee.db')
# cursor = conn.cursor()

# # See approved sick leaves
# cursor.execute("""
# SELECT id, leave_days, status
# FROM leave_requests
# WHERE leave_type = 'Sick Leave'
#   AND status = 'Approved'
# """)

# print(cursor.fetchall())

# # Example: change one record to Rejected
# cursor.execute("""
# UPDATE leave_requests
# SET status = 'Rejected'
# WHERE id = 27
# """)

# conn.commit()
# conn.close()

# print('Fixed sick leave data')


# import sqlite3

# conn = sqlite3.connect('employee.db')
# cursor = conn.cursor()

# cursor.execute("PRAGMA table_info(leave_types)")

# for row in cursor.fetchall():
#     print(row)

# conn.close()

# import sqlite3

# # conn = sqlite3.connect('employee.db')
# # cursor = conn.cursor()
# from database import get_connection

# conn = get_connection()
# cursor = conn.cursor()

# cursor.execute("SELECT employee_id, name, whatsapp FROM employees")

# for row in cursor.fetchall():
#     print(row)

# conn.close()


# import sqlite3

# conn = sqlite3.connect('data/employee.db')
# cursor = conn.cursor()

# cursor.execute("""
# UPDATE salary_slips
# SET file_path = 'uploads/salary_slips/EMP001_June_2026.pdf'
# WHERE employee_id = 'EMP001'
# """)

# conn.commit()
# conn.close()

# print('Salary path updated')

import sqlite3

conn = sqlite3.connect("data/employee.db")
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
print(cursor.fetchall())

conn.close()