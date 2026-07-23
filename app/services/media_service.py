import os

import re
import logging
from database import get_latest_salary_slip
from app.services.whatsapp_service import (
    send_text,
    send_document,
    send_video
)
import re

from database import get_salary_slip_by_month
# Properly initialize the logger using the current module name
logger = logging.getLogger(__name__)



def handle_health_insurance_video(employee, base_url):
    sender = employee['whatsapp']
    employee_id = employee['employee_id']

    try:
        video_url = (
            base_url.rstrip('/')
            + '/static/videos/health_insurance_claim.mp4'
        )

        send_video(
            to=sender,
            video_url=video_url,
            caption='📹 Health Insurance Claim Process'
        )

        logger.info(
            f'HEALTH_INSURANCE_VIDEO_SENT | user={employee_id}'
        )

    except Exception as e:
        logger.exception(
            f'HEALTH_INSURANCE_VIDEO_ERROR | '
            f'user={employee_id} | error={str(e)}'
        )

        send_text(
            sender,
            '❌ Error sending insurance video.'
        )

def handle_salary_slip(employee, message):
    """
    Send salary slip over WhatsApp.
    
    Examples:
    - "give my salary slip" → latest
    - "give my march salary slip" → March
    - "give my april 2026 salary slip" → April 2026
    """

    sender = employee["whatsapp"]
    employee_id = employee["employee_id"]

    try:
        logger.info(
            f'SALARY_SLIP_REQUEST | user={employee_id} | message="{message}"'
        )

        # ---------------------------------
        # Extract month and year
        # ---------------------------------
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
            "september": "September", "sep": "September", "sept": "September",
            "october": "October", "oct": "October",
            "november": "November", "nov": "November",
            "december": "December", "dec": "December"
        }

        msg = message.lower()

        # Check for matching month (full name or abbreviation)
        for key, value in months_map.items():
            if re.search(rf'\b{key}\b', msg):
                month = value
                break

        # Match 4-digit year starting with 20 (e.g., 2026)
        year_match = re.search(r'\b(20\d{2})\b', msg)
        if year_match:
            year = int(year_match.group(1))

        logger.info(
            f'SALARY_QUERY_PARSED | user={employee_id} | month={month} | year={year}'
        )

        # ---------------------------------
        # Fetch salary slip
        # ---------------------------------
        if month:
            path = get_salary_slip_by_month(employee_id, month, year)
        else:
            path = get_latest_salary_slip(employee_id)

        # ---------------------------------
        # Handle missing slip
        # ---------------------------------
        if not path:
            logger.warning(
                f'SALARY_SLIP_NOT_FOUND | user={employee_id} | month={month} | year={year}'
            )

            if month:
                year_str = f" {year}" if year else ""
                send_text(
                    sender,
                    f"❌ No salary slip found for {month}{year_str}."
                )
            else:
                send_text(sender, "❌ No salary slip found.")

            return

        # ---------------------------------
        # Normalize file path and send PDF
        # ---------------------------------
        caption = (
            f"Your {month} salary slip"
            if month else
            "Your latest salary slip"
        )

        logger.info(
            f'SALARY_SLIP_FOUND | user={employee_id} | file={path}'
        )

        # Convert DB path to actual uploads path
        # DB stores: salary_slips/EMP001_June_2026.pdf
        # Actual file: uploads/salary_slips/EMP001_June_2026.pdf
        final_path = path
        if not final_path.startswith('uploads/'):
            final_path = os.path.join('uploads', final_path)

        logger.info(
            f'FINAL_SALARY_PATH | user={employee_id} | path={final_path}'
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
        send_text(sender, "⚠️ An error occurred while retrieving your salary slip. Please try again later.")