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

def send_document(to, file_path, filename):
    url = f'https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/media'

    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}'
    }

    try:
        with open(file_path, 'rb') as f:
            files = {'file': (filename, f, 'application/pdf')}
            data = {'messaging_product': 'whatsapp'}

            response = requests.post(
                url,
                headers=headers,
                files=files,
                data=data,
                timeout=60
            )

        if response.status_code != 200:
            logger.error(
                f'MEDIA_UPLOAD_FAILED | response={response.text}'
            )
            return

        media_id = response.json()['id']

        message_url = (
            f'https://graph.facebook.com/v23.0/'
            f'{PHONE_NUMBER_ID}/messages'
        )
        to = format_whatsapp_number(to)

        payload = {
            'messaging_product': 'whatsapp',
            'to': to,
            'type': 'document',
            'document': {
                'id': media_id,
                'filename': filename
            }
        }

        response = requests.post(
            message_url,
            headers={
                'Authorization': f'Bearer {WHATSAPP_TOKEN}',
                'Content-Type': 'application/json'
            },
            json=payload,
            timeout=30
        )

        logger.info(
            f'WHATSAPP_DOCUMENT_SENT | to={to} | file={filename}'
        )

    except Exception:
        logger.exception(
            f'WHATSAPP_DOCUMENT_EXCEPTION | to={to} | file={filename}'
        )

def send_video(to, video_url, caption=None):
    url = (
        f'https://graph.facebook.com/v23.0/'
        f'{PHONE_NUMBER_ID}/messages'
    )

    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'video',
        'video': {
            'link': video_url
        }
    }

    if caption:
        payload['video']['caption'] = caption

    response = requests.post(
        url,
        headers={
            'Authorization': f'Bearer {WHATSAPP_TOKEN}',
            'Content-Type': 'application/json'
        },
        json=payload,
        timeout=30
    )

    if response.status_code == 200:
        logger.info(f'WHATSAPP_VIDEO_SENT | to={to}')
    else:
        logger.error(
            f'WHATSAPP_VIDEO_FAILED | '
            f'status={response.status_code} | '
            f'response={response.text}'
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