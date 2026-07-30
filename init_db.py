import os
import psycopg2

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_B9x8LFtZqMHh@ep-long-voice-azn7jllb.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require",
)

if DATABASE_URL.startswith("postgres://"):
  DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

try:
  # Employees
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS employees (
        employee_id VARCHAR(50) PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        whatsapp VARCHAR(20) UNIQUE NOT NULL,
        manager VARCHAR(100),
        department VARCHAR(100)
    );
    """)

  cursor.executemany(
      """
    INSERT INTO employees (employee_id, name, whatsapp, manager, department)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (employee_id) DO NOTHING;
    """,
      [
          ("EMP001", "Yash", "918600945888", "Priya Sharma", "AI"),
          ("EMP002", "Rahul", "919876543210", "Amit Kumar", "HR"),
          ("EMP003", "Sneha", "919999999999", "Priya Sharma", "Finance"),
      ],
  )

  # Leave Types
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS leave_types (
        id SERIAL PRIMARY KEY,
        leave_name VARCHAR(50) NOT NULL UNIQUE,
        yearly_limit INTEGER NOT NULL
    );
    """)

  cursor.executemany(
      """
    INSERT INTO leave_types (leave_name, yearly_limit)
    VALUES (%s, %s)
    ON CONFLICT (leave_name) DO NOTHING;
    """,
      [("Casual Leave", 12), ("Sick Leave", 10), ("Earned Leave", 15)],
  )

  # Leave Balance
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS leave_balance (
        employee_id VARCHAR(50) PRIMARY KEY REFERENCES employees(employee_id),
        casual INTEGER DEFAULT 12,
        sick INTEGER DEFAULT 10,
        earned INTEGER DEFAULT 15
    );
    """)

  cursor.executemany(
      """
    INSERT INTO leave_balance (employee_id, casual, sick, earned)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (employee_id) DO NOTHING;
    """,
      [("EMP001", 8, 6, 15), ("EMP002", 10, 8, 18), ("EMP003", 12, 10, 20)],
  )

  # Salary Slips
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS salary_slips (
        id SERIAL PRIMARY KEY,
        employee_id VARCHAR(50) NOT NULL REFERENCES employees(employee_id),
        month INTEGER NOT NULL,
        year INTEGER NOT NULL,
        file_path TEXT NOT NULL
    );
    """)

  # Leave Requests
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS leave_requests (
        id SERIAL PRIMARY KEY,
        employee_id VARCHAR(50) NOT NULL REFERENCES employees(employee_id),
        from_date VARCHAR(20) NOT NULL,
        to_date VARCHAR(20) NOT NULL,
        leave_days INTEGER,
        leave_type VARCHAR(50) DEFAULT 'Casual Leave' REFERENCES leave_types(leave_name),
        reason TEXT,
        category VARCHAR(50),
        priority VARCHAR(20) DEFAULT 'Normal',
        status VARCHAR(20) DEFAULT 'Pending',
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

  # Activity Logs
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs (
        id SERIAL PRIMARY KEY,
        activity TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

  # Training Videos
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS training_videos (
        id SERIAL PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        category VARCHAR(100) NOT NULL,
        description TEXT,
        s3_key TEXT NOT NULL,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

  # Policy Files
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS policy_files (
        id SERIAL PRIMARY KEY,
        file_name VARCHAR(255) NOT NULL UNIQUE,
        s3_key TEXT NOT NULL,
        version VARCHAR(50) NOT NULL,
        file_hash VARCHAR(100) NOT NULL,
        upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status VARCHAR(20) DEFAULT 'active'
    );
    """)

  conn.commit()
  print("✅ PostgreSQL Database Initialized Successfully!")

finally:
  cursor.close()
  conn.close()