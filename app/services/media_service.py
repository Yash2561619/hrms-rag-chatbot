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



import os

def handle_health_insurance_video(employee):
    """Send health insurance claim process video."""

    sender = employee['whatsapp']
    employee_id = employee['employee_id']

    try:
        # Local file path on Render
        video_path = os.path.join(
            'uploads',
            'videos',
            'health_insurance_claim.mp4'
        )

        logger.info(
            f'VIDEO_REQUEST | user={employee_id} | path={video_path}'
        )

        # Check file exists
        if not os.path.exists(video_path):

            logger.error(
                f'VIDEO_FILE_NOT_FOUND | path={video_path}'
            )

            send_text(
                sender,
                '❌ Health insurance video is not available right now.'
            )
            return

        # Send video to WhatsApp
        send_video(sender, video_path)

        logger.info(
            f'HEALTH_INSURANCE_VIDEO_SENT | user={employee_id}'
        )

    except Exception:

        logger.exception(
            f'HEALTH_INSURANCE_VIDEO_ERROR | user={employee_id}'
        )

        send_text(
            sender,
            '❌ Error sending insurance video.'
        )


from app.services.s3_service import send_document_from_s3

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
            if re.search(rf'\\b{key}\\b', msg):
                month = value
                break

        year_match = re.search(r'\\b(20\\d{2})\\b', msg)
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