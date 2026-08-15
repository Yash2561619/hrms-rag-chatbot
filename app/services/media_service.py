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

  Supported categories: Health Insurance, Safety, Induction.
  """
  sender = employee["whatsapp"]
  employee_id = (
      employee.get("employee_id")
      if isinstance(employee, dict)
      else employee[1]
  )

  try:
    msg = message.lower().strip()

    # 1. Detect requested category
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
      # Send list menu or prompt asking to choose a category
      send_text(
          sender,
          "🎥 *Please choose a training video category:*\n\n"
          "• *Health Insurance* (e.g., _'Show health insurance video'_)\n"
          "• *Safety* (e.g., _'Send safety video'_)\n"
          "• *Induction* (e.g., _'Watch induction video'_)",
      )
      return

    logger.info(
        f"VIDEO_CATEGORY | user={employee_id} | category={category}"
    )

    # 2. Fetch video from database
    video = get_training_video_by_category(category)

    if not video:
      logger.warning(
          f"VIDEO_NOT_FOUND | user={employee_id} | category={category}"
      )
      send_text(sender, f"❌ *{category}* training video is currently not available.")
      return

    title, s3_key = video[0], video[1]
    logger.info(f"VIDEO_FOUND | user={employee_id} | key={s3_key}")

    # 3. Generate Presigned URL & Deliver
    video_url = generate_presigned_url(s3_key)
    if not video_url:
      send_text(sender, "❌ Unable to generate video link. Please try again later.")
      return

    send_video(sender, video_url, caption=f"📹 *{title}*")
    logger.info(
        f"TRAINING_VIDEO_SENT | user={employee_id} | category={category}"
    )

  except Exception:
    logger.exception(f"TRAINING_VIDEO_ERROR | user={employee_id}")
    send_text(sender, "❌ Error retrieving training video.")


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


from app.services.whatsapp_service import send_interactive_list

def handle_greeting(employee: dict):
    """Sends a greeting with an interactive list picker."""
    sender = employee["whatsapp"]
    raw_name = employee.get("name", "there") if isinstance(employee, dict) else employee[2]
    name = raw_name.split()[0]

    body_text = f"👋 Hello *{name}*! I am your AI HR Assistant.\n\nTap the button below to choose an action, or simply ask any company policy question."
    
    sections = [
        {
            "title": "📋 Leave Management",
            "rows": [
                {
                    "id": "action_apply_leave",
                    "title": "🌴 Apply for Leave",
                    "description": "Request casual, sick, or earned leave"
                },
                {
                    "id": "action_leave_balance",
                    "title": "📊 Check Leave Balance",
                    "description": "View remaining CL, SL, and EL"
                },
                {
                    "id": "action_leave_history",
                    "title": "📜 View Leave History",
                    "description": "See past and pending requests"
                }
            ]
        },
        {
            "title": "💼 Payroll & Documents",
            "rows": [
                {
                    "id": "action_salary_slip",
                    "title": "💰 Get Salary Slip",
                    "description": "Download your latest password-protected payslip"
                },
                {
                    "id": "action_training_videos",
                    "title": "🎥 Training Videos",
                    "description": "Watch policy & induction guides"
                }
            ]
        }
    ]

    send_interactive_list(
        recipient_id=sender,
        body_text=body_text,
        button_label="Explore Menu 📋",
        sections=sections,
        header_text="🏢 HR Services Menu"
    )

def extract_message_info(data: dict):
    """Extracts text or interactive button/list selection from WhatsApp webhook."""
    try:
        entry = data["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages", [])
        if not messages:
            return None, None

        msg_obj = messages[0]
        sender = msg_obj.get("from")
        msg_type = msg_obj.get("type")

        # 1. Plain Text Message
        if msg_type == "text":
            return sender, msg_obj["text"]["body"]

        # 2. Interactive List Selection or Button Click
        elif msg_type == "interactive":
            interactive_obj = msg_obj.get("interactive", {})
            itype = interactive_obj.get("type")

            if itype == "list_reply":
                # Returns the 'id' (e.g., 'action_apply_leave')
                return sender, interactive_obj["list_reply"]["id"]

            elif itype == "button_reply":
                # Returns the 'id' (e.g., 'btn_confirm_leave')
                return sender, interactive_obj["button_reply"]["id"]

        return sender, None
    except Exception as e:
        logger.error(f"WEBHOOK_PARSE_ERROR | {e}")
        return None, None