import logging
import os
import re

from app.services.s3_service import generate_presigned_url
from app.services.whatsapp_service import (
    send_document,
    send_text,
    send_video,
)
from database import (
    get_latest_salary_slip,
    get_salary_slip_by_month,
    get_training_video_by_category,
)

logger = logging.getLogger(__name__)


def handle_training_video(employee, message):
  """Send training video based on user request.

  Supported categories:
  - Health Insurance
  - Safety
  - Induction
  """

  sender = employee["whatsapp"]
  employee_id = employee["employee_id"]

  try:
    msg = message.lower()

    # ----------------------------
    # Detect requested category
    # ----------------------------
    if any(
        word in msg
        for word in ["insurance", "health insurance", "claim", "medical"]
    ):
      category = "Health Insurance"

    elif any(
        word in msg for word in ["safety", "fire", "security", "emergency"]
    ):
      category = "Safety"

    elif any(
        word in msg
        for word in ["induction", "joining", "onboarding", "welcome"]
    ):
      category = "Induction"

    else:
      send_text(
          sender,
          "❌ Please specify which training video you need.\n\n"
          "Examples:\n"
          "• Health Insurance\n"
          "• Safety\n"
          "• Induction",
      )
      return

    logger.info(
        f"VIDEO_CATEGORY | user={employee_id} | category={category}"
    )

    # ----------------------------
    # Fetch video from database
    # ----------------------------
    video = get_training_video_by_category(category)

    if not video:
      logger.warning(
          f"VIDEO_NOT_FOUND | user={employee_id} | category={category}"
      )

      send_text(sender, f"❌ {category} video is not available.")
      return

    title, s3_key = video[0], video[1]

    logger.info(f"VIDEO_FOUND | user={employee_id} | key={s3_key}")

    # ----------------------------
    # Generate S3 URL
    # ----------------------------
    video_url = generate_presigned_url(s3_key)

    logger.info(f"VIDEO_URL_GENERATED | user={employee_id}")

    # ----------------------------
    # Send WhatsApp video
    # ----------------------------
    send_video(sender, video_url, caption=f"📹 {title}")

    logger.info(
        f"TRAINING_VIDEO_SENT | user={employee_id} | category={category}"
    )

  except Exception:
    logger.exception(f"TRAINING_VIDEO_ERROR | user={employee_id}")

    send_text(sender, "❌ Error sending training video.")


def handle_salary_slip(employee, message):
  sender = employee["whatsapp"]
  employee_id = (
      employee.get("employee_id")
      if isinstance(employee, dict)
      else employee[1]
  )
  raw_phone = (
      employee.get("whatsapp") if isinstance(employee, dict) else employee[5]
  )

  try:
    logger.info(
        f'SALARY_SLIP_REQUEST | user={employee_id} | message="{message}"'
    )

    month = None
    year = None

    months_map = {
        "january": "January",
        "jan": "January",
        "february": "February",
        "feb": "February",
        "march": "March",
        "mar": "March",
        "april": "April",
        "apr": "April",
        "may": "May",
        "june": "June",
        "jun": "June",
        "july": "July",
        "jul": "July",
        "august": "August",
        "aug": "August",
        "september": "September",
        "sep": "September",
        "october": "October",
        "oct": "October",
        "november": "November",
        "nov": "November",
        "december": "December",
        "dec": "December",
    }

    msg = message.lower()

    for key, value in months_map.items():
      if re.search(rf"\b{key}\b", msg):
        month = value
        break

    year_match = re.search(r"\b(20\d{2})\b", msg)
    if year_match:
      year = int(year_match.group(1))

    logger.info(
        f"SALARY_QUERY_PARSED | user={employee_id} | month={month} |"
        f" year={year}"
    )

    # 1. Fetch salary slip path from database
    if month:
      path = get_salary_slip_by_month(employee_id, month, year)
    else:
      path = get_latest_salary_slip(employee_id)

    # 2. Not found handling
    if not path:
      logger.warning(
          f"SALARY_SLIP_NOT_FOUND | user={employee_id} | month={month} |"
          f" year={year}"
      )
      if month:
        send_text(sender, f"❌ No salary slip found for {month}.")
      else:
        send_text(sender, "❌ No salary slip found.")
      return

    logger.info(f"SALARY_SLIP_FOUND | user={employee_id} | file={path}")

    # 3. Build dynamic password example using employee's actual details
    digits_only = re.sub(r"\D", "", str(raw_phone or ""))
    last_4_phone = digits_only[-4:] if len(digits_only) >= 4 else "XXXX"
    example_password = f"{employee_id}@{last_4_phone}"

    # 4. Construct WhatsApp caption with password instructions
    slip_period = (
        f"{month} {year}" if (month and year) else (month or "Latest")
    )
    caption = f"""📄 *Salary Slip - {slip_period}*

Your password-protected salary slip is attached below.

🔒 *Password to Open:*
`{employee_id}@<Last 4 digits of your registered mobile number>`
_Example:_ `{example_password}`"""

    # 5. Deliver document (Generate presigned URL for S3 or use local file path)
    if path.startswith("salary_slips/"):
      logger.info(f"S3_SALARY_PATH | user={employee_id} | key={path}")
      presigned_url = generate_presigned_url(path)
      if not presigned_url:
        send_text(
            sender,
            "❌ Unable to generate download link. Please try again later.",
        )
        return
      send_document(sender, presigned_url, caption=caption)
    else:
      final_path = path
      if not final_path.startswith("uploads/"):
        final_path = os.path.join("uploads", final_path)

      logger.info(
          f"LOCAL_SALARY_PATH | user={employee_id} | path={final_path}"
      )
      send_document(sender, final_path, caption=caption)

    logger.info(
        f"SALARY_SLIP_SENT | user={employee_id} | month={month} | year={year}"
    )

  except Exception as e:
    logger.error(
        f"SALARY_SLIP_ERROR | user={employee_id} | error={str(e)}",
        exc_info=True,
    )
    send_text(
        sender, "⚠️ Error fetching salary slip. Please try again later."
    )


def handle_greeting(employee: dict):
    """Sends an introduction and interactive menu when an employee greets the bot."""
    sender = employee["whatsapp"]
    name = employee.get("name", "there").split()[0]  # First name

    welcome_message = f"""👋 *Hello {name}!*

I am your **AI HR Assistant**. I'm here to help you manage your workplace requests and answer HR policy questions instantly.

---

📌 *Here is what I can do for you:*

1️⃣ *Apply for Leave*
   • e.g., _"I need leave from 20\u200b-08\u200b-2026 to 22\u200b-08\u200b-2026 for personal work"_

2️⃣ *Check Leave Balance*
   • e.g., _"What is my leave balance?"_

3️⃣ *Check Leave History*
   • e.g., _"Show my leave history"_

4️⃣ *Get Salary Slips*
   • e.g., _"Give my latest salary slip"_ or _"Send June salary slip"_

5️⃣ *Training & Induction Videos*
   • e.g., _"Send training video"_ or _"Show health insurance video"_

6️⃣ *HR & Company Policy Q&A*
   • e.g., _"What is the standard probation period?"_
   • e.g., _"What is the travel reimbursement policy?"_

---

💬 *How can I help you today?*"""

    send_text(sender, welcome_message)
    logger.info(
        f"GREETING_SENT | user={employee.get('employee_id')} | name={name}"
    )