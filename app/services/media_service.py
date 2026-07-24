import logging
import re
import os
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

# Properly initialize the logger using the current module name
logger = logging.getLogger(__name__)

def handle_training_video(employee, message):
    """
    Send training video based on user request.

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
        if any(word in msg for word in [
            "insurance",
            "health insurance",
            "claim"
        ]):
            category = "Health Insurance"

        elif any(word in msg for word in [
            "safety",
            "fire",
            "security"
        ]):
            category = "Safety"

        elif any(word in msg for word in [
            "induction",
            "joining",
            "onboarding"
        ]):
            category = "Induction"

        else:
            send_text(
                sender,
                "❌ Please specify which training video you need.\n\n"
                "Examples:\n"
                "• Health Insurance\n"
                "• Safety\n"
                "• Induction"
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

            send_text(
                sender,
                f"❌ {category} video is not available."
            )
            return

        title, s3_key = video

        logger.info(
            f"VIDEO_FOUND | user={employee_id} | key={s3_key}"
        )

        # ----------------------------
        # Generate S3 URL
        # ----------------------------
        video_url = generate_presigned_url(s3_key)

        logger.info(
            f"VIDEO_URL_GENERATED | user={employee_id}"
        )

        # ----------------------------
        # Send WhatsApp video
        # ----------------------------
        send_video(
            sender,
            video_url,
            caption=f"📹 {title}"
        )

        logger.info(
            f"TRAINING_VIDEO_SENT | user={employee_id} | category={category}"
        )

    except Exception:
        logger.exception(
            f"TRAINING_VIDEO_ERROR | user={employee_id}"
        )

        send_text(
            sender,
            "❌ Error sending training video."
        )

def handle_salary_slip(employee, message):
    sender = employee["whatsapp"]
    employee_id = employee["employee_id"]

    try:
        logger.info(
            f'SALARY_SLIP_REQUEST | user={employee_id} | message="{message}"'
        )

        # Extract month and year
        month = None
        year = None

        months_map = {
            "january": "January", "jan": "January",
            "february": "February", "feb": "February",
            "march": "March", "mar": "March",
            "april": "April", "apr": "April",
            "may": "May",
            "june": "June", "jun": "June",
            "july": "July", "jul": "July",
            "august": "August", "aug": "August",
            "september": "September", "sep": "September",
            "october": "October", "oct": "October",
            "november": "November", "nov": "November",
            "december": "December", "dec": "December"
        }

        msg = message.lower()

        for key, value in months_map.items():
            if re.search(rf'\b{key}\b', msg):
                month = value
                break

        year_match = re.search(r'\b(20\d{2})\b', msg)
        if year_match:
            year = int(year_match.group(1))

        logger.info(
            f'SALARY_QUERY_PARSED | user={employee_id} | month={month} | year={year}'
        )

        # Fetch salary slip
        if month:
            path = get_salary_slip_by_month(employee_id, month, year)
        else:
            path = get_latest_salary_slip(employee_id)

        # Not found
        if not path:
            logger.warning(
                f'SALARY_SLIP_NOT_FOUND | user={employee_id} | month={month} | year={year}'
            )

            if month:
                send_text(sender, f'❌ No salary slip found for {month}.')
            else:
                send_text(sender, '❌ No salary slip found.')

            return

        logger.info(
            f'SALARY_SLIP_FOUND | user={employee_id} | file={path}'
        )

        caption = (
            f'Your {month} salary slip'
            if month else
            'Your latest salary slip'
        )

        # ==============================
        # S3 FILE
        # ==============================
        if path.startswith('salary_slips/'):

            logger.info(
                f'S3_SALARY_PATH | user={employee_id} | key={path}'
            )


            send_document(sender, path, caption)

        # ==============================
        # LOCAL FILE
        # ==============================
        else:

            final_path = path

            if not final_path.startswith('uploads/'):
                final_path = os.path.join('uploads', final_path)

            logger.info(
                f'LOCAL_SALARY_PATH | user={employee_id} | path={final_path}'
            )

            send_document(sender, final_path, caption)

        logger.info(
            f'SALARY_SLIP_SENT | user={employee_id} | month={month} | year={year}'
        )

    except Exception as e:
        logger.error(
            f'SALARY_SLIP_ERROR | user={employee_id} | error={str(e)}',
            exc_info=True
        )

        send_text(
            sender,
            '⚠️ Error fetching salary slip. Please try again later.'
        )