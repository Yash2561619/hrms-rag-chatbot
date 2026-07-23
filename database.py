import sqlite3
import os
from datetime import datetime
from zoneinfo import ZoneInfo
DB_NAME = os.path.join('data', 'employee.db')


def get_connection():
    return sqlite3.connect(DB_NAME)


def initialize_database():
    conn = get_connection()
    cursor = conn.cursor()

    # Employees table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS employees(
        employee_id TEXT PRIMARY KEY,
        name TEXT,
        whatsapp TEXT UNIQUE,
        manager TEXT,
        department TEXT
    )
    """)

    # Salary slips table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS salary_slips(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id TEXT NOT NULL,
        month INTEGER NOT NULL,
        year INTEGER NOT NULL,
        file_path TEXT NOT NULL,
        FOREIGN KEY(employee_id)
        REFERENCES employees(employee_id)
    )
    """)
     # =====================================================
# Activity Logs
# =====================================================

    cursor.execute("""
         CREATE TABLE IF NOT EXISTS activity_logs(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         activity TEXT NOT NULL,
         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
   
# Leave Balance Table
# =====================================================
      

    

    conn.commit()
    conn.close()


# Find employee by WhatsApp number
def get_employee_by_whatsapp(number):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT employee_id, name, whatsapp, manager, department
    FROM employees
    WHERE whatsapp = ?
    """, (number,))

    row = cursor.fetchone()

    conn.close()

    if row is None:
        return None

    return {
        "employee_id": row[0],
        "name": row[1],
        "whatsapp": row[2],
        "manager": row[3],
        "department": row[4]
    }


# Get latest salary slip
def get_latest_salary_slip(employee_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT file_path
    FROM salary_slips
    WHERE employee_id = ?
    ORDER BY year DESC, month DESC
    LIMIT 1
    """, (employee_id,))

    row = cursor.fetchone()

    conn.close()

    if row is None:
        return None

    return row[0]


def get_leave_balance(employee_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT casual,sick,earned
    FROM leave_balance
    WHERE employee_id=?
    """,(employee_id,))

    row=cursor.fetchone()

    conn.close()

    if row is None:
        return None

    return{
        "casual":row[0],
        "sick":row[1],
        "earned":row[2]
    }


def apply_leave(
    employee_id,
    from_date,
    to_date,
    leave_days,
    reason,
    leave_type,
    priority
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO leave_requests(
            employee_id,
            from_date,
            to_date,
            leave_days,
            reason,
            leave_type,
            priority
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
    (
        employee_id,
        from_date,
        to_date,
        leave_days,
        reason,
        leave_type,
        priority
    ))

    conn.commit()
    conn.close()

def get_dashboard_stats():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM employees")
    employees = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM leave_requests
        WHERE status='Pending'
    """)
    pending = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM salary_slips
    """)
    salary = cursor.fetchone()[0]

    conn.close()

    policy_count = len([
        f for f in os.listdir("uploads/policies")
        if f.lower().endswith(".pdf")
    ])

    return {
        "employees": employees,
        "pending": pending,
        "salary": salary,
        "policies": policy_count
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
                AND DATE('now') BETWEEN l.from_date AND l.to_date
            )
            THEN 'On Leave'
            ELSE 'Active'
        END AS employee_status

    FROM employees e
    """

    if search:

        query += """
        WHERE
            e.employee_id LIKE ?
            OR e.name LIKE ?
            OR e.whatsapp LIKE ?
            OR e.department LIKE ?
            OR e.manager LIKE ?
        ORDER BY e.employee_id
        """

        cursor.execute(
            query,
            (
                f"%{search}%",
                f"%{search}%",
                f"%{search}%",
                f"%{search}%",
                f"%{search}%"
            )
        )

    else:

        query += """
        ORDER BY e.employee_id
        """

        cursor.execute(query)

    rows = cursor.fetchall()

    conn.close()

    return rows
def add_employee(employee_id, name, whatsapp, manager, department):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO employees(
        employee_id,
        name,
        whatsapp,
        manager,
        department
    )
    VALUES(?,?,?,?,?)
    """,
    (
        employee_id,
        name,
        whatsapp,
        manager,
        department
    ))

    conn.commit()
    conn.close()



def update_leave_status(request_id, status):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE leave_requests
    SET status = ?
    WHERE id = ?
    """, (status, request_id))

    conn.commit()
    conn.close()

def get_leave_details(request_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
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
    WHERE l.id = ?
    """, (request_id,))

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
        "status": row[9]
    }

import sqlite3
import logging

# Configure logging for structured output (better practice than raw print statements)
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")


def save_salary_slip(employee_id: int, month: int, year: int, file_path: str) -> None:
    """Saves or updates an employee's salary slip record in the database.

    Args:
        employee_id: Unique identifier for the employee.
        month: Month of the salary slip (e.g., 1 to 12).
        year: Year of the salary slip (e.g., 2026).
        file_path: Relative or absolute path to the stored salary slip file.
    """
    logging.debug(
        f"[DEBUG] emp_id: {employee_id}, month: {month} ({type(month).__name__}), "
        f"year: {year} ({type(year).__name__}), path: {file_path}"
    )

    conn = get_connection()

    try:
        # Use connection as a context manager to handle automatic transaction commit/rollback
        with conn:
            cursor = conn.cursor()

            # Check if salary slip already exists
            cursor.execute(
                """
                SELECT id 
                FROM salary_slips 
                WHERE employee_id = ? AND month = ? AND year = ?
                """,
                (employee_id, month, year),
            )
            existing_record = cursor.fetchone()

            if existing_record:
                # Update existing record
                record_id = existing_record[0]
                cursor.execute(
                    """
                    UPDATE salary_slips 
                    SET file_path = ? 
                    WHERE id = ?
                    """,
                    (file_path, record_id),
                )
                logging.info(f"Updated salary slip [ID: {record_id}] for employee {employee_id}.")
            else:
                # Insert new record
                cursor.execute(
                    """
                    INSERT INTO salary_slips (employee_id, month, year, file_path)
                    VALUES (?, ?, ?, ?)
                    """,
                    (employee_id, month, year, file_path),
                )
                logging.info(f"Inserted new salary slip for employee {employee_id}.")

    except sqlite3.Error as e:
        logging.error(f"Database error while saving salary slip for employee {employee_id}: {e}")
        raise

    finally:
        # Always close connection to prevent leaking resources
        conn.close()
def get_leave_history(employee_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT
        from_date,
        to_date,
        category,
        status
    FROM leave_requests
    WHERE employee_id = ?
    ORDER BY applied_at DESC
    """, (employee_id,))

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

    result = {
        "Pending": 0,
        "Approved": 0,
        "Rejected": 0
    }

    for status, count in rows:
        result[status] = count

    return result

def get_employee(employee_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            employee_id,
            name,
            whatsapp,
            manager,
            department
        FROM employees
        WHERE employee_id=?
    """, (employee_id,))

    row = cursor.fetchone()

    conn.close()

    return row


def update_employee(employee_id, name, whatsapp, manager, department):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE employees
        SET
            name=?,
            whatsapp=?,
            manager=?,
            department=?
        WHERE employee_id=?
    """, (
        name,
        whatsapp,
        manager,
        department,
        employee_id
    ))

    conn.commit()
    conn.close()


def delete_employee(employee_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM employees
        WHERE employee_id=?
    """, (employee_id,))

    conn.commit()
    conn.close()

def get_monthly_leave_data():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            strftime('%m', applied_at) AS month,
            COUNT(*)
        FROM leave_requests
        GROUP BY month
        ORDER BY month
    """)

    rows = cursor.fetchall()
    conn.close()

    months = [
        "Jan","Feb","Mar","Apr","May","Jun",
        "Jul","Aug","Sep","Oct","Nov","Dec"
    ]

    data = [0] * 12

    for month, count in rows:
        if month:
            data[int(month)-1] = count

    return {
        "labels": months,
        "values": data
    }

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
        "values": [row[1] for row in rows]
    }

def log_activity(activity):

    conn = get_connection()
    cursor = conn.cursor()

    current_time = datetime.now(
        ZoneInfo("Asia/Kolkata")
    ).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO activity_logs(
            activity,
            created_at
        )
        VALUES(?, ?)
    """, (
        activity,
        current_time
    ))

    conn.commit()
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
            l.id,
            e.name,
            l.leave_type,
            l.from_date,
            l.to_date,
            l.category,
            l.reason,
            l.status,
            l.priority
        FROM leave_requests l
        JOIN employees e
            ON l.employee_id = e.employee_id
        ORDER BY l.applied_at DESC
    """)

    rows = cursor.fetchall()

    conn.close()

    return rows

def get_leave_summary():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM leave_requests
        WHERE status='Pending'
    """)
    pending = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM leave_requests
        WHERE status='Approved'
    """)
    approved = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM leave_requests
        WHERE
            date('now') BETWEEN from_date AND to_date
            AND status='Approved'
    """)
    active = cursor.fetchone()[0]

    conn.close()

    return {
        "pending": pending,
        "approved": approved,
        "active": active
    }

def get_all_salary_slips():

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            s.id,
            e.name,
            s.month,
            s.year,
            s.file_path
        FROM salary_slips s
        JOIN employees e
            ON s.employee_id = e.employee_id
        ORDER BY s.year DESC, s.month DESC
    """)

    rows = cursor.fetchall()

    conn.close()

    salary_slips = []

    for row in rows:

        salary_slips.append({

            "id": row[0],

            "employee_name": row[1],

            "month": row[2],

            "year": row[3],

            "file_path": row[4]

        })

    return salary_slips

def delete_salary_slip(id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT file_path FROM salary_slips WHERE id=?",
        (id,)
    )

    row = cursor.fetchone()

    if not row:
        conn.close()
        return None

    file_path = row[0]

    cursor.execute(
        "DELETE FROM salary_slips WHERE id=?",
        (id,)
    )

    conn.commit()
    conn.close()

    return file_path

def get_leave_types():

    conn = get_connection() 
    cursor = conn.cursor()
    cursor.execute("""
    SELECT leave_name, yearly_limit FROM leave_types ORDER BY leave_name """)
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_used_leaves(employee_id):
    conn = get_connection() 
    cursor = conn.cursor()
    cursor.execute(""" SELECT leave_type, COALESCE(SUM(leave_days), 0) 
                   FROM leave_requests WHERE employee_id = ? AND status = 'Approved' 
                   GROUP BY leave_type """, (employee_id,))
    rows = cursor.fetchall() 
    conn.close() 
    used = {} 
    for leave_type, days in rows: 
        used[leave_type] = int(days) 
    return used

def can_approve_leave(employee_id, leave_type, requested_days):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT yearly_limit
        FROM leave_types
        WHERE leave_name = ?
    """, (leave_type,))

    row = cursor.fetchone()

    if not row:
        conn.close()
        return False, 'Leave type not found'

    yearly_limit = row[0]

    cursor.execute("""
        SELECT COALESCE(SUM(leave_days), 0)
        FROM leave_requests
        WHERE employee_id = ?
          AND leave_type = ?
          AND status = 'Approved'
    """, (employee_id, leave_type))

    used = cursor.fetchone()[0]

    remaining = yearly_limit - used

    conn.close()

    if requested_days > remaining:
        return False, f'Only {remaining} {leave_type} day(s) remaining'

    return True, 'Sufficient balance'


def get_salary_slip_by_month(employee_id, month, year=None):
    conn = get_connection()
    cursor = conn.cursor()

    # Convert month name to number
    month_map = {
        'January': 1,
        'February': 2,
        'March': 3,
        'April': 4,
        'May': 5,
        'June': 6,
        'July': 7,
        'August': 8,
        'September': 9,
        'October': 10,
        'November': 11,
        'December': 12
    }

    month_num = month_map.get(month)

    print(f'DEBUG QUERY: employee={employee_id}, month={month}, month_num={month_num}, year={year}')

    if month_num is None:
        conn.close()
        return None

    if year:
        cursor.execute("""
            SELECT month, year, file_path
            FROM salary_slips
            WHERE employee_id = ?
              AND month = ?
              AND year = ?
            ORDER BY id DESC
            LIMIT 1
        """, (employee_id, month_num, year))
    else:
        cursor.execute("""
            SELECT month, year, file_path
            FROM salary_slips
            WHERE employee_id = ?
              AND month = ?
            ORDER BY year DESC, id DESC
            LIMIT 1
        """, (employee_id, month_num))

    row = cursor.fetchone()
    print('DEBUG RESULT:', row)

    conn.close()

    return row[2] if row else None