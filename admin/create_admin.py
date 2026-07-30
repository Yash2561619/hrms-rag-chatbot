import os
import psycopg2
from werkzeug.security import generate_password_hash

# Fetch Database URL from environment or paste your Neon connection string
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_B9x8LFtZqMHh@ep-long-voice-azn7jllb.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require",
)

if DATABASE_URL.startswith("postgres://"):
  DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

try:
  # 1. Create Admins Table
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        email VARCHAR(100) UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role VARCHAR(50) DEFAULT 'HR Manager',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

  # 2. Setup Admin Details
  NAME = "Yash Kabure"
  EMAIL = "admin@apexhr.com"
  PASSWORD = "Yash@2005"
  password_hash = generate_password_hash(PASSWORD)

  # 3. Upsert Admin User
  cursor.execute(
      """
    INSERT INTO admins (name, email, password_hash, role)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (email) DO UPDATE
    SET password_hash = EXCLUDED.password_hash;
    """,
      (NAME, EMAIL, password_hash, "HR Manager"),
  )

  conn.commit()

  print("✅ Admin table ensured on PostgreSQL")
  print("✅ Admin account ensured")
  print("Email:", EMAIL)

finally:
  cursor.close()
  conn.close()