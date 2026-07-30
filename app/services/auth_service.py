import logging
from werkzeug.security import check_password_hash
from database import get_connection

logger = logging.getLogger(__name__)


def authenticate_admin(email, password):
  """Verify admin credentials against PostgreSQL database."""

  print("\n========== AUTH DEBUG ==========")
  print("Input email:", email)

  conn = get_connection()
  cursor = conn.cursor()

  try:
    # Updated '?' to PostgreSQL placeholder '%s'
    cursor.execute(
        """
        SELECT id, name, email, password_hash, role
        FROM admins
        WHERE email = %s
        """,
        (email,),
    )

    admin = cursor.fetchone()

  finally:
    cursor.close()
    conn.close()

  print("DB result:", admin)

  if not admin:
    print("❌ Admin not found")
    return None

  admin_id, name, email, password_hash, role = admin

  is_valid = check_password_hash(password_hash, password)

  print("Password valid:", is_valid)
  print("================================\n")

  if is_valid:
    return {
        "id": admin_id,
        "name": name,
        "email": email,
        "role": role,
    }

  return None