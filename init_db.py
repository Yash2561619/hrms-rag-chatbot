import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Ensure data directory exists
os.makedirs(BASE_DIR / 'data', exist_ok=True)

DB_PATH = BASE_DIR / 'data' / 'employee.db'

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()


# =====================================================
# Create Employees Table
# =====================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS employees (
    employee_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    whatsapp TEXT UNIQUE NOT NULL,
    manager TEXT,
    department TEXT
)
""")

# =====================================================
# Insert Sample Employees
# =====================================================

cursor.executemany("""
INSERT OR IGNORE INTO employees
(employee_id, name, whatsapp, manager, department)
VALUES (?, ?, ?, ?, ?)
""", [
    ("EMP001", "Yash", "918600945888", "Priya Sharma", "AI"),
    ("EMP002", "Rahul", "919876543210", "Amit Kumar", "HR"),
    ("EMP003", "Sneha", "919999999999", "Priya Sharma", "Finance")
])

# =====================================================
# Create Salary Slips Table
# =====================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS salary_slips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL,
    month INTEGER NOT NULL,
    year INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    FOREIGN KEY(employee_id) REFERENCES employees(employee_id)
)
""")

# =====================================================
# Insert Sample Salary Slips
# =====================================================

cursor.executemany("""
INSERT OR IGNORE INTO salary_slips
(employee_id, month, year, file_path)
VALUES (?, ?, ?, ?)
""", [
    ("EMP001", 6, 2026, "salary_slips/EMP001_June_2026.pdf"),
    ("EMP002", 6, 2026, "salary_slips/EMP002_June_2026.pdf"),
    ("EMP003", 6, 2026, "salary_slips/EMP003_June_2026.pdf")
])

# =====================================================
# Create Activity Logs Table (MISSING IN YOUR CODE!)
# =====================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

# =====================================================
# Create Leave Balance Table
# =====================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS leave_balance (
    employee_id TEXT PRIMARY KEY,
    casual INTEGER DEFAULT 12,
    sick INTEGER DEFAULT 10,
    earned INTEGER DEFAULT 15,
    FOREIGN KEY(employee_id) REFERENCES employees(employee_id)
)
""")

cursor.executemany("""
INSERT OR IGNORE INTO leave_balance
(employee_id, casual, sick, earned)
VALUES (?, ?, ?, ?)
""", [
    ("EMP001", 8, 6, 15),
    ("EMP002", 10, 8, 18),
    ("EMP003", 12, 10, 20)
])

# =====================================================
# Create Leave Types Table
# =====================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS leave_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    leave_name TEXT NOT NULL UNIQUE,
    yearly_limit INTEGER NOT NULL
)
""")

cursor.executemany("""
INSERT OR IGNORE INTO leave_types
(leave_name, yearly_limit)
VALUES (?, ?)
""", [
    ("Casual Leave", 12),
    ("Sick Leave", 10),
    ("Earned Leave", 15)
])

# =====================================================
# Create Leave Requests Table (WITH leave_type COLUMN)
# =====================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS leave_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL,
    from_date TEXT NOT NULL,
    to_date TEXT NOT NULL,
    leave_days INTEGER,
    leave_type TEXT DEFAULT 'Casual Leave',
    reason TEXT,
    category TEXT,
    priority TEXT DEFAULT 'Normal',
    status TEXT DEFAULT 'Pending',
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(employee_id) REFERENCES employees(employee_id),
    FOREIGN KEY(leave_type) REFERENCES leave_types(leave_name)
)
""")

# =====================================================
# Insert Sample Leave Requests (IMPORTANT!)
# =====================================================

cursor.executemany("""
INSERT OR IGNORE INTO leave_requests
(employee_id, from_date, to_date, leave_days, leave_type, reason, category, priority, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""", [
    ("EMP001", "2026-07-01", "2026-07-05", 5, "Casual Leave", "Personal", "personal", "Normal", "Pending"),
    ("EMP002", "2026-07-10", "2026-07-12", 3, "Sick Leave", "Fever", "sick", "Normal", "Approved"),
    ("EMP003", "2026-07-15", "2026-07-20", 6, "Earned Leave", "Vacation", "vacation", "Normal", "Pending")
])

cursor.execute("""
CREATE TABLE IF NOT EXISTS training_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    s3_key TEXT NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
# =====================================================
# Save Changes
# =====================================================

conn.commit()
conn.close()

print("✅ Database created successfully!")
print("✅ Tables created:")
print("   - employees")
print("   - salary_slips")
print("   - activity_logs")
print("   - leave_balance")
print("   - leave_types")
print("   - leave_requests")
print("\n✅ Sample data inserted for 3 employees")