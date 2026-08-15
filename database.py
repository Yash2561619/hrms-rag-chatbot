from datetime import datetime
import logging
import os
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

# Load Database URL from Environment Variable (Fallback to Neon default string)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_B9x8LFtZqMHh@ep-long-voice-azn7jllb.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require",
)

# Render and Neon URLs start with "postgres://", but psycopg2 handles both cleanly
if DATABASE_URL.startswith("postgres://"):
  DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


def get_connection():
  """Establishes connection to PostgreSQL database."""
  return psycopg2.connect(DATABASE_URL)


def initialize_database():
  """Creates required PostgreSQL tables if they don't exist."""
  conn = get_connection()
  cursor = conn.cursor()

  # Employees table
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS employees(
        employee_id VARCHAR(50) PRIMARY KEY,
        name VARCHAR(100),
        whatsapp VARCHAR(20) UNIQUE,
        manager VARCHAR(100),
        department VARCHAR(100)
    );
    """)

  # Salary slips table
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS salary_slips(
        id SERIAL PRIMARY KEY,
        employee_id VARCHAR(50) NOT NULL REFERENCES employees(employee_id),
        month INTEGER NOT NULL,
        year INTEGER NOT NULL,
        file_path TEXT NOT NULL
    );
    """)

  # Activity Logs table
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs(
        id SERIAL PRIMARY KEY,
        activity TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

  # Leave Balance Table
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS leave_balance(
        employee_id VARCHAR(50) PRIMARY KEY REFERENCES employees(employee_id),
        casual INTEGER DEFAULT 12,
        sick INTEGER DEFAULT 10,
        earned INTEGER DEFAULT 15
    );
    """)

  # Leave Types Table
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS leave_types(
        id SERIAL PRIMARY KEY,
        leave_name VARCHAR(50) NOT NULL UNIQUE,
        yearly_limit INTEGER NOT NULL
    );
    """)

  # Leave Requests Table
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS leave_requests(
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

  # Training Videos Table
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS training_videos(
        id SERIAL PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        category VARCHAR(100) NOT NULL,
        description TEXT,
        s3_key TEXT NOT NULL,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

  # Policy Files Table
  cursor.execute("""
    CREATE TABLE IF NOT EXISTS policy_files(
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
  cursor.close()
  conn.close()


def get_employee_by_whatsapp(number):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
    SELECT employee_id, name, whatsapp, manager, department
    FROM employees
    WHERE whatsapp = %s
    """,
      (number,),
  )

  row = cursor.fetchone()
  conn.close()

  if row is None:
    return None

  return {
      "employee_id": row[0],
      "name": row[1],
      "whatsapp": row[2],
      "manager": row[3],
      "department": row[4],
  }


def get_latest_salary_slip(employee_id):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
    SELECT file_path
    FROM salary_slips
    WHERE employee_id = %s
    ORDER BY year DESC, month DESC
    LIMIT 1
    """,
      (employee_id,),
  )

  row = cursor.fetchone()
  conn.close()

  return row[0] if row else None


def get_leave_balance(employee_id):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
    SELECT casual, sick, earned
    FROM leave_balance
    WHERE employee_id = %s
    """,
      (employee_id,),
  )

  row = cursor.fetchone()
  conn.close()

  if row is None:
    return None

  return {"casual": row[0], "sick": row[1], "earned": row[2]}


def apply_leave(
    employee_id, from_date, to_date, leave_days, reason, leave_type, priority
):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
        INSERT INTO leave_requests(
            employee_id, from_date, to_date, leave_days, reason, leave_type, priority
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """,
      (
          employee_id,
          from_date,
          to_date,
          leave_days,
          reason,
          leave_type,
          priority,
      ),
  )

  conn.commit()
  cursor.close()
  conn.close()


def get_dashboard_stats():
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute("SELECT COUNT(*) FROM employees;")
  employees = cursor.fetchone()[0]

  cursor.execute("SELECT COUNT(*) FROM leave_requests WHERE status='Pending';")
  pending = cursor.fetchone()[0]

  cursor.execute("SELECT COUNT(*) FROM salary_slips;")
  salary = cursor.fetchone()[0]

  conn.close()

  policy_count = 0
  if os.path.exists("uploads/policies"):
    policy_count = len([
        f
        for f in os.listdir("uploads/policies")
        if f.lower().endswith(".pdf")
    ])

  return {
      "employees": employees,
      "pending": pending,
      "salary": salary,
      "policies": policy_count,
  }


def get_all_employees(search=""):
  conn = get_connection()
  cursor = conn.cursor()

  query = """
    SELECT
        e.employee_id,
        e.name,
        e.whatsapp,
        e.department,
        e.manager,
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM leave_requests l
                WHERE l.employee_id = e.employee_id
                AND l.status = 'Approved'
                AND CURRENT_DATE BETWEEN CAST(l.from_date AS DATE) AND CAST(l.to_date AS DATE)
            )
            THEN 'On Leave'
            ELSE 'Active'
        END AS employee_status
    FROM employees e
    """

  if search:
    query += """
        WHERE
            e.employee_id ILIKE %s
            OR e.name ILIKE %s
            OR e.whatsapp ILIKE %s
            OR e.department ILIKE %s
            OR e.manager ILIKE %s
        ORDER BY e.employee_id
        """
    param = f"%{search}%"
    cursor.execute(query, (param, param, param, param, param))
  else:
    query += " ORDER BY e.employee_id"
    cursor.execute(query)

  rows = cursor.fetchall()
  conn.close()
  return rows


def add_employee(employee_id, name, whatsapp, manager, department):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
    INSERT INTO employees(employee_id, name, whatsapp, manager, department)
    VALUES(%s, %s, %s, %s, %s)
    """,
      (employee_id, name, whatsapp, manager, department),
  )

  conn.commit()
  cursor.close()
  conn.close()


def update_leave_status(request_id, status):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
    UPDATE leave_requests
    SET status = %s
    WHERE id = %s
    """,
      (status, request_id),
  )

  conn.commit()
  cursor.close()
  conn.close()


def get_leave_details(request_id):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
    SELECT
        l.employee_id,
        e.name,
        e.whatsapp,
        e.manager,
        l.from_date,
        l.to_date,
        l.leave_days,
        l.leave_type,
        l.reason,
        l.status
    FROM leave_requests l
    JOIN employees e ON l.employee_id = e.employee_id
    WHERE l.id = %s
    """,
      (request_id,),
  )

  row = cursor.fetchone()
  conn.close()

  if row is None:
    return None

  return {
      "employee_id": row[0],
      "name": row[1],
      "whatsapp": row[2],
      "manager": row[3],
      "from_date": row[4],
      "to_date": row[5],
      "leave_days": row[6],
      "leave_type": row[7],
      "reason": row[8],
      "status": row[9],
  }


def save_salary_slip(
    employee_id: str, month: int, year: int, file_path: str
) -> None:
  conn = get_connection()
  try:
    with conn:
      cursor = conn.cursor()
      cursor.execute(
          """
                SELECT id 
                FROM salary_slips 
                WHERE employee_id = %s AND month = %s AND year = %s
                """,
          (employee_id, month, year),
      )
      existing_record = cursor.fetchone()

      if existing_record:
        record_id = existing_record[0]
        cursor.execute(
            """
                    UPDATE salary_slips 
                    SET file_path = %s 
                    WHERE id = %s
                    """,
            (file_path, record_id),
        )
        logging.info(
            f"Updated salary slip [ID: {record_id}] for employee {employee_id}."
        )
      else:
        cursor.execute(
            """
                    INSERT INTO salary_slips (employee_id, month, year, file_path)
                    VALUES (%s, %s, %s, %s)
                    """,
            (employee_id, month, year, file_path),
        )
        logging.info(f"Inserted new salary slip for employee {employee_id}.")
  except Exception as e:
    logging.error(
        f"Database error while saving salary slip for employee {employee_id}:"
        f" {e}"
    )
    raise
  finally:
    conn.close()


def get_leave_history(employee_id):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
    SELECT from_date, to_date, category, status
    FROM leave_requests
    WHERE employee_id = %s
    ORDER BY applied_at DESC
    """,
      (employee_id,),
  )

  rows = cursor.fetchall()
  conn.close()
  return rows


def get_leave_status_counts():
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute("""
        SELECT status, COUNT(*)
        FROM leave_requests
        GROUP BY status
    """)

  rows = cursor.fetchall()
  conn.close()

  result = {"Pending": 0, "Approved": 0, "Rejected": 0}

  for status, count in rows:
    if status in result:
      result[status] = count

  return result


def get_employee(employee_id):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
        SELECT employee_id, name, whatsapp, manager, department
        FROM employees
        WHERE employee_id = %s
    """,
      (employee_id,),
  )

  row = cursor.fetchone()
  conn.close()
  return row


def update_employee(employee_id, name, whatsapp, manager, department):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
        UPDATE employees
        SET name = %s, whatsapp = %s, manager = %s, department = %s
        WHERE employee_id = %s
    """,
      (name, whatsapp, manager, department, employee_id),
  )

  conn.commit()
  cursor.close()
  conn.close()

logger = logging.getLogger(__name__)
def delete_employee(employee_id):
  conn = None
  try:
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Delete dependent child records first
    cursor.execute(
        "DELETE FROM leave_requests WHERE employee_id = %s", (employee_id,)
    )

    # 2. Delete salary slips if applicable
    try:
      cursor.execute(
          "DELETE FROM salary_slips WHERE employee_id = %s", (employee_id,)
      )
    except Exception:
      pass

    # 3. Delete the employee record
    cursor.execute(
        "DELETE FROM employees WHERE employee_id = %s", (employee_id,)
    )

    conn.commit()
    logger.info(f"EMPLOYEE_DELETED | employee_id={employee_id}")
    return True

  except Exception as e:
    if conn:
      conn.rollback()
    logger.exception(f"DELETE_EMPLOYEE_ERROR | employee_id={employee_id}")
    raise e
  finally:
    if conn:
      cursor.close()
      conn.close()


def get_monthly_leave_data():
  conn = get_connection()
  cursor = conn.cursor()

  # TO_CHAR handles month extraction in PostgreSQL
  cursor.execute("""
        SELECT TO_CHAR(applied_at, 'MM') AS month, COUNT(*)
        FROM leave_requests
        GROUP BY month
        ORDER BY month
    """)

  rows = cursor.fetchall()
  conn.close()

  months = [
      "Jan",
      "Feb",
      "Mar",
      "Apr",
      "May",
      "Jun",
      "Jul",
      "Aug",
      "Sep",
      "Oct",
      "Nov",
      "Dec",
  ]
  data = [0] * 12

  for month, count in rows:
    if month:
      data[int(month) - 1] = count

  return {"labels": months, "values": data}


def get_department_employee_counts():
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute("""
        SELECT department, COUNT(*)
        FROM employees
        GROUP BY department
        ORDER BY COUNT(*) DESC
    """)

  rows = cursor.fetchall()
  conn.close()

  return {
      "labels": [row[0] for row in rows],
      "values": [row[1] for row in rows],
  }


def log_activity(activity):
  conn = get_connection()
  cursor = conn.cursor()

  current_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime(
      "%Y-%m-%d %H:%M:%S"
  )

  cursor.execute(
      """
        INSERT INTO activity_logs(activity, created_at)
        VALUES(%s, %s)
    """,
      (activity, current_time),
  )

  conn.commit()
  cursor.close()
  conn.close()


def get_recent_activities():
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute("""
        SELECT activity, created_at
        FROM activity_logs
        ORDER BY created_at DESC
        LIMIT 10
    """)

  rows = cursor.fetchall()
  conn.close()
  return rows


def get_all_leave_requests():
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute("""
        SELECT
            l.id, e.name, l.leave_type, l.from_date, l.to_date,
            l.category, l.reason, l.status, l.priority
        FROM leave_requests l
        JOIN employees e ON l.employee_id = e.employee_id
        ORDER BY l.applied_at DESC
    """)

  rows = cursor.fetchall()
  conn.close()
  return rows


def get_leave_summary():
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      "SELECT COUNT(*) FROM leave_requests WHERE status='Pending'"
  )
  pending = cursor.fetchone()[0]

  cursor.execute(
      "SELECT COUNT(*) FROM leave_requests WHERE status='Approved'"
  )
  approved = cursor.fetchone()[0]

  cursor.execute("""
        SELECT COUNT(*)
        FROM leave_requests
        WHERE CURRENT_DATE BETWEEN CAST(from_date AS DATE) AND CAST(to_date AS DATE)
          AND status='Approved'
    """)
  active = cursor.fetchone()[0]

  conn.close()
  return {"pending": pending, "approved": approved, "active": active}


def get_all_salary_slips():
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute("""
        SELECT s.id, e.name, s.month, s.year, s.file_path
        FROM salary_slips s
        JOIN employees e ON s.employee_id = e.employee_id
        ORDER BY s.year DESC, s.month DESC, e.name
    """)

  rows = cursor.fetchall()
  conn.close()

  return [{
      "id": row[0],
      "employee_name": row[1],
      "month": row[2],
      "year": row[3],
      "file_path": row[4],
  } for row in rows]


def delete_salary_slip(id):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute("SELECT file_path FROM salary_slips WHERE id=%s", (id,))
  row = cursor.fetchone()

  if not row:
    conn.close()
    return None

  file_path = row[0]
  cursor.execute("DELETE FROM salary_slips WHERE id=%s", (id,))
  conn.commit()
  conn.close()
  return file_path


def get_leave_types():
  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT leave_name, yearly_limit FROM leave_types ORDER BY leave_name"
  )
  rows = cursor.fetchall()
  conn.close()
  return rows


def get_used_leaves(employee_id):
  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute(
      """
        SELECT leave_type, COALESCE(SUM(leave_days), 0)
        FROM leave_requests 
        WHERE employee_id = %s AND status = 'Approved'
        GROUP BY leave_type
    """,
      (employee_id,),
  )
  rows = cursor.fetchall()
  conn.close()
  return {leave_type: int(days) for leave_type, days in rows}


def can_approve_leave(employee_id, leave_type, requested_days):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      "SELECT yearly_limit FROM leave_types WHERE leave_name = %s", (leave_type,)
  )
  row = cursor.fetchone()

  if not row:
    conn.close()
    return False, "Leave type not found"

  yearly_limit = row[0]

  cursor.execute(
      """
        SELECT COALESCE(SUM(leave_days), 0)
        FROM leave_requests
        WHERE employee_id = %s AND leave_type = %s AND status = 'Approved'
    """,
      (employee_id, leave_type),
  )

  used = cursor.fetchone()[0]
  remaining = yearly_limit - used
  conn.close()

  if requested_days > remaining:
    return False, f"Only {remaining} {leave_type} day(s) remaining"

  return True, "Sufficient balance"


def get_salary_slip_by_month(employee_id, month, year=None):
  conn = get_connection()
  cursor = conn.cursor()

  month_map = {
      "January": 1,
      "February": 2,
      "March": 3,
      "April": 4,
      "May": 5,
      "June": 6,
      "July": 7,
      "August": 8,
      "September": 9,
      "October": 10,
      "November": 11,
      "December": 12,
  }

  month_num = month_map.get(month)
  if month_num is None:
    conn.close()
    return None

  if year:
    cursor.execute(
        """
            SELECT month, year, file_path
            FROM salary_slips
            WHERE employee_id = %s AND month = %s AND year = %s
            ORDER BY id DESC LIMIT 1
        """,
        (employee_id, month_num, year),
    )
  else:
    cursor.execute(
        """
            SELECT month, year, file_path
            FROM salary_slips
            WHERE employee_id = %s AND month = %s
            ORDER BY year DESC, id DESC LIMIT 1
        """,
        (employee_id, month_num),
    )

  row = cursor.fetchone()
  conn.close()
  return row[2] if row else None


def save_training_video(title, category, s3_key):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
        INSERT INTO training_videos (title, category, s3_key)
        VALUES (%s, %s, %s)
    """,
      (title, category, s3_key),
  )

  conn.commit()
  conn.close()


def get_training_video(title):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
    SELECT * FROM training_videos
    WHERE LOWER(title) = LOWER(%s)
    LIMIT 1
    """,
      (title,),
  )

  row = cursor.fetchone()
  conn.close()
  return row


def get_all_training_videos():
  conn = get_connection()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT * FROM training_videos ORDER BY uploaded_at DESC"
  )
  rows = cursor.fetchall()
  conn.close()
  return rows


def get_training_video_by_category(category):
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
    SELECT title, s3_key FROM training_videos
    WHERE LOWER(category) = LOWER(%s)
    ORDER BY uploaded_at DESC LIMIT 1
    """,
      (category,),
  )

  row = cursor.fetchone()
  conn.close()
  return row


def save_policy_file(file_name, s3_key, version, file_hash):
  """Save or update policy metadata in PostgreSQL."""
  conn = get_connection()
  cursor = conn.cursor()

  cursor.execute(
      """
        INSERT INTO policy_files (file_name, s3_key, version, file_hash)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT(file_name) DO UPDATE SET
            s3_key = EXCLUDED.s3_key,
            version = EXCLUDED.version,
            file_hash = EXCLUDED.file_hash,
            upload_time = CURRENT_TIMESTAMP,
            status = 'active'
    """,
      (file_name, s3_key, version, file_hash),
  )

  conn.commit()
  conn.close()


def get_all_policy_files():
  """Retrieve all policy records as dictionary objects."""
  conn = get_connection()
  cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

  cursor.execute("""
        SELECT file_name, s3_key, version, file_hash
        FROM policy_files
        ORDER BY id DESC
    """)

  rows = [dict(row) for row in cursor.fetchall()]
  conn.close()
  return rows



def delete_policy_file(filename):
  """Deletes policy metadata record from PostgreSQL database."""
  conn = get_connection()
  cursor = conn.cursor()

  try:
    cursor.execute(
        """
            DELETE FROM policy_files
            WHERE file_name = %s
        """,
        (filename,),
    )
    conn.commit()
    logging.info(f"DB_POLICY_DELETED | file={filename}")
  except Exception as e:
    logging.error(f"DB_POLICY_DELETE_ERROR | file={filename} | error={e}")
    conn.rollback()
    raise e
  finally:
    cursor.close()
    conn.close()


def delete_training_video(video_id):
  """Deletes a training video record from PostgreSQL and returns its S3 key."""
  conn = get_connection()
  cursor = conn.cursor()

  try:
    # 1. Fetch S3 key first
    cursor.execute(
        "SELECT s3_key FROM training_videos WHERE id = %s", (video_id,)
    )
    row = cursor.fetchone()

    if not row:
      return None

    s3_key = row[0]

    # 2. Delete record
    cursor.execute("DELETE FROM training_videos WHERE id = %s", (video_id,))
    conn.commit()
    logging.info(f"DB_VIDEO_DELETED | id={video_id}")
    return s3_key

  except Exception as e:
    logging.error(f"DB_VIDEO_DELETE_ERROR | id={video_id} | error={e}")
    conn.rollback()
    raise e
  finally:
    cursor.close()
    conn.close()