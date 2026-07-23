import requests
import logging

logger = logging.getLogger(__name__)

# These values will be injected from app.py
WHATSAPP_TOKEN = None
PHONE_NUMBER_ID = None

def configure(token, phone_number_id):
    global WHATSAPP_TOKEN, PHONE_NUMBER_ID
    WHATSAPP_TOKEN = token
    PHONE_NUMBER_ID = phone_number_id

def send_text(to, message):
    url = f'https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages'

    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}',
        'Content-Type': 'application/json'
    }
    to = format_whatsapp_number(to)
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'text',
        'text': {'body': message}
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30
        )

        logger.info(
            f'WHATSAPP_TEXT_SENT | to={to} | status={response.status_code}'
        )

        if response.status_code != 200:
            logger.error(
                f'WHATSAPP_TEXT_FAILED | to={to} | response={response.text}'
            )

    except requests.RequestException:
        logger.exception(f'WHATSAPP_TEXT_EXCEPTION | to={to}')

from app.services.s3_service import generate_download_url
import requests
import logging

logger = logging.getLogger(__name__)

def send_document(to, file_path, caption):
    """
    Send document via WhatsApp.
    Supports both:
    - AWS S3 keys (salary_slips/...)
    - Local files (fallback)
    """

    try:
        # ==========================================
        # S3 DOCUMENT
        # ==========================================
        if file_path.startswith('salary_slips/'):

            signed_url = generate_download_url(file_path)

            payload = {
                'messaging_product': 'whatsapp',
                'to': to,
                'type': 'document',
                'document': {
                    'link': signed_url,
                    'caption': caption,
                    'filename': file_path.split('/')[-1]
                }
            }

            headers = {
                'Authorization': f'Bearer {WHATSAPP_TOKEN}',
                'Content-Type': 'application/json'
            }

            url = f'https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages'

            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=30
            )

            logger.info(
                f'WHATSAPP_S3_DOCUMENT_SENT | to={to} | status={response.status_code}'
            )

            if response.status_code != 200:
                logger.error(response.text)

            return response.status_code == 200

        # ==========================================
        # LOCAL FILE (fallback)
        # ==========================================
        with open(file_path, 'rb') as f:
            logger.info(f'LOCAL_FILE_SENT | path={file_path}')
            return True

    except Exception:
        logger.exception(
            f'WHATSAPP_DOCUMENT_EXCEPTION | to={to} | file={caption}'
        )
        return False
def send_video(to, file_path):
    url = f'https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/media'

    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}'
    }

    with open(file_path, 'rb') as f:
        files = {
            'file': ('video.mp4', f, 'video/mp4')
        }
        data = {
            'messaging_product': 'whatsapp'
        }

        response = requests.post(
            url,
            headers=headers,
            files=files,
            data=data,
            timeout=120
        )

    media_id = response.json()['id']

    message_url = (
        f'https://graph.facebook.com/v23.0/'
        f'{PHONE_NUMBER_ID}/messages'
    )

    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'video',
        'video': {
            'id': media_id,
            'caption': '📹 Health Insurance Claim Process'
        }
    }

    requests.post(
        message_url,
        headers={
            'Authorization': f'Bearer {WHATSAPP_TOKEN}',
            'Content-Type': 'application/json'
        },
        json=payload,
        timeout=30
    )
def mark_read(message_id):
    url = f'https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages'

    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}',
        'Content-Type': 'application/json'
    }

    payload = {
        'messaging_product': 'whatsapp',
        'status': 'read',
        'message_id': message_id
    }

    try:
        requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=15
        )

    except Exception:
        logger.exception(
            f'MARK_READ_EXCEPTION | message_id={message_id}'
        )

def format_whatsapp_number(phone: str) -> str:
    """
    Convert DB number to WhatsApp API format.
    DB stores: 8600945888
    API needs: 918600945888
    """

    phone = str(phone).strip()

    # Already international
    if phone.startswith("91") and len(phone) == 12:
        return phone

    # Convert 10-digit Indian number
    if len(phone) == 10 and phone.isdigit():
        return f"91{phone}"

    return phone