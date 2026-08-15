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
    Send document via WhatsApp Cloud API.
    Supports:
    - Pre-signed URLs / HTTP links (https://...)
    - AWS S3 Keys (salary_slips/...)
    - Local fallback files
    """
    try:
        url = f'https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages'
        headers = {
            'Authorization': f'Bearer {WHATSAPP_TOKEN}',
            'Content-Type': 'application/json'
        }

        # --------------------------------------------------
        # 1. ALREADY A PRESIGNED URL / WEB LINK
        # --------------------------------------------------
        if file_path.startswith('http://') or file_path.startswith('https://'):
            # Extract clean filename or set default
            filename = file_path.split('?')[0].split('/')[-1] or "Salary_Slip.pdf"

            payload = {
                'messaging_product': 'whatsapp',
                'to': str(to),
                'type': 'document',
                'document': {
                    'link': file_path,
                    'caption': caption,
                    'filename': filename
                }
            }

            response = requests.post(url, headers=headers, json=payload, timeout=30)
            logger.info(f'WHATSAPP_URL_DOCUMENT_SENT | to={to} | status={response.status_code}')

            if response.status_code != 200:
                logger.error(f"WHATSAPP_SEND_ERROR | {response.text}")
            return response.status_code == 200

        # --------------------------------------------------
        # 2. AWS S3 KEY (salary_slips/...)
        # --------------------------------------------------
        elif file_path.startswith('salary_slips/'):
            signed_url = generate_download_url(file_path)

            payload = {
                'messaging_product': 'whatsapp',
                'to': str(to),
                'type': 'document',
                'document': {
                    'link': signed_url,
                    'caption': caption,
                    'filename': file_path.split('/')[-1]
                }
            }

            response = requests.post(url, headers=headers, json=payload, timeout=30)
            logger.info(f'WHATSAPP_S3_DOCUMENT_SENT | to={to} | status={response.status_code}')

            if response.status_code != 200:
                logger.error(f"WHATSAPP_SEND_ERROR | {response.text}")
            return response.status_code == 200

        # --------------------------------------------------
        # 3. LOCAL FILE FALLBACK (Uploads via Media API)
        # --------------------------------------------------
        else:
            if not os.path.exists(file_path):
                logger.error(f"LOCAL_FILE_NOT_FOUND | path={file_path}")
                return False

            media_upload_url = f'https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/media'
            filename = os.path.basename(file_path)

            with open(file_path, 'rb') as f:
                files = {
                    'file': (filename, f, 'application/pdf'),
                    'messaging_product': (None, 'whatsapp'),
                    'type': (None, 'application/pdf'),
                }
                media_headers = {'Authorization': f'Bearer {WHATSAPP_TOKEN}'}
                media_resp = requests.post(media_upload_url, headers=media_headers, files=files, timeout=30)
                
                if media_resp.status_code != 200:
                    logger.error(f"MEDIA_UPLOAD_FAILED | {media_resp.text}")
                    return False
                
                media_id = media_resp.json().get('id')

            payload = {
                'messaging_product': 'whatsapp',
                'to': str(to),
                'type': 'document',
                'document': {
                    'id': media_id,
                    'caption': caption,
                    'filename': filename
                }
            }

            response = requests.post(url, headers=headers, json=payload, timeout=30)
            logger.info(f'WHATSAPP_LOCAL_MEDIA_SENT | to={to} | status={response.status_code}')
            return response.status_code == 200

    except Exception:
        logger.exception(f'WHATSAPP_DOCUMENT_EXCEPTION | to={to} | file={file_path}')
        return False

    
import requests
import logging

logger = logging.getLogger(__name__)

def send_video(to, video_url, caption="📹 Training Video"):
    """
    Send video from S3 URL to WhatsApp.

    Args:
        to (str): WhatsApp number.
        video_url (str): S3 presigned URL.
        caption (str): Video caption.
    """

    try:

        url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "video",
            "video": {
                "link": video_url,
                "caption": caption
            }
        }

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=60
        )

        if response.status_code != 200:
            logger.error(
                f"WHATSAPP_VIDEO_FAILED | status={response.status_code} | response={response.text}"
            )
            return False

        logger.info(
            f"WHATSAPP_VIDEO_SENT | to={to} | status={response.status_code}"
        )

        return True

    except Exception:
        logger.exception(
            f"WHATSAPP_VIDEO_EXCEPTION | to={to}"
        )
        return False
    
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