"""
WhatsApp HR Assistant - Main Flask Application
Webhook server with routing to RAG, leave handling, media dispatch, etc.
Uses Google Gemini API only (no Anthropic).
"""

import os
import logging

# Disable ChromaDB telemetry before importing chromadb
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import chromadb
from chromadb.utils import embedding_functions
import google.genai as genai
from flask import Flask, request, send_from_directory

from config import Config
from rate_limiter import check_rate_limit
from database import get_employee_by_whatsapp, initialize_database
from leave_session import leave_sessions
from scripts.update_db import build_index

from app.routes.admin_routes import admin_bp
from app.services.rag_service import handle_rag_query
from app.services import leave_service
from app.services.intent_service import classify_intent
from app.services.whatsapp_service import configure, mark_read, send_text
from app.services.leave_service import (
    start_leave_conversation,
    continue_leave_conversation,
    handle_leave_balance,
    handle_leave_history
)
from app.services.media_service import (
    handle_salary_slip,
    handle_health_insurance_video
)

os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(
            'logs/hr_chatbot.log',
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

app = Flask(__name__)

app.config.from_object(Config)
app.register_blueprint(admin_bp)
app.secret_key = 'apexhr-super-secret-key-2026'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_TYPE'] = 'filesystem'

POLICY_FOLDER = app.config['POLICY_FOLDER']
SALARY_FOLDER = app.config['SALARY_FOLDER']
VIDEO_FOLDER = app.config['VIDEO_FOLDER']

app.secret_key = app.config['SECRET_KEY']

GEMINI_API_KEY = app.config['GEMINI_API_KEY']
WHATSAPP_TOKEN = app.config['WHATSAPP_TOKEN']
PHONE_NUMBER_ID = app.config['PHONE_NUMBER_ID']
WABA_ID = app.config['WABA_ID']
VERIFY_TOKEN = app.config['VERIFY_TOKEN']

print('=' * 50)
print('CONFIG DEBUG')
print('WHATSAPP_TOKEN exists:', bool(WHATSAPP_TOKEN))
print('PHONE_NUMBER_ID:', PHONE_NUMBER_ID)
print('WABA_ID:', WABA_ID)
print('VERIFY_TOKEN exists:', bool(VERIFY_TOKEN))
print('=' * 50)

configure(
    WHATSAPP_TOKEN,
    PHONE_NUMBER_ID
)

os.makedirs(POLICY_FOLDER, exist_ok=True)
os.makedirs(SALARY_FOLDER, exist_ok=True)
os.makedirs(VIDEO_FOLDER, exist_ok=True)

logger.info('Configuration loaded successfully')

# Global objects initialized at startup
collection = None
gemini_client = None


def init_gemini():
    """Initialize Gemini client at startup."""
    global gemini_client

    if GEMINI_API_KEY:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        logger.info('Gemini client initialized')
    else:
        logger.error('GEMINI_API_KEY not set')


def init_chroma():
    """Initialize persistent Chroma collection used by the whole app."""
    global collection

    try:
        ef = embedding_functions.DefaultEmbeddingFunction()

        # SAME PATH as update_db.py
        client = chromadb.PersistentClient(path='chroma_db')

        # IMPORTANT: do NOT create a new collection
        collection = client.get_or_create_collection(
            name='hr_policies',
            embedding_function=ef
        )

        count = collection.count()

        logger.info(f'Chroma initialized | chunks={count}')

        if count == 0:
            logger.warning(
                'Knowledge base is empty. Run: python -m scripts.update_db'
            )

    except Exception:
        logger.exception('CHROMA_INIT_ERROR')
        collection = None


def router(employee, message):
    intent = classify_intent(message)
    sender = employee["whatsapp"]

    logger.info(f"Intent classified: {intent}")

    try:
        # ===============================
        # Existing leave conversation
        # ===============================
        if sender in leave_sessions:
            msg = message.lower().strip()

            if msg in ["cancel", "stop", "exit", "quit", "never mind", "forget it"]:
                del leave_sessions[sender]
                send_text(sender, "❌ Leave request cancelled.")
                return

            if msg in ["restart", "reset", "start over"]:
                leave_sessions[sender] = {
                    "from_date": None,
                    "to_date": None,
                    "reason": None,
                    "waiting_for": "from_date"
                }

                send_text(
                    sender,
                    "🔄 Let's start again.\n\n📅 From which date would you like to start your leave?"
                )
                return

            # ALWAYS continue the leave conversation
            continue_leave_conversation(employee, message, gemini_client)
            return

        # ===============================
        # Normal routing
        # ===============================

        if intent == "rag":
            handle_rag_query(employee, message, collection, gemini_client)

        elif intent == "leave_balance":
            handle_leave_balance(employee)

        elif intent == "apply_leave":
            start_leave_conversation(employee, message, gemini_client)

        elif intent == "salary_slip":
            handle_salary_slip(employee, message)

        elif intent == "training_video":
            handle_training_video(employee, message)

        elif intent == "leave_history":
            handle_leave_history(employee)

        else:
            send_text(sender, "I didn't understand your request.")

    except Exception:
        logger.exception(
            f'ROUTER_ERROR | user={employee["employee_id"]}'
        )
        send_text(
            sender,
            '❌ Something went wrong while processing your request.'
        )


@app.route('/webhook', methods=['GET'])
def verify():
    if (
        request.args.get('hub.mode') == 'subscribe'
        and request.args.get('hub.verify_token') == VERIFY_TOKEN
    ):
        logger.info('WEBHOOK_VERIFIED')
        return request.args.get('hub.challenge'), 200

    logger.warning('WEBHOOK_VERIFICATION_FAILED')
    return 'Forbidden', 403


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        body = request.get_json(silent=True) or {}

        # =====================================================
        # VALIDATE META PAYLOAD
        # =====================================================
        entry = body.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})

        # =====================================================
        # IGNORE STATUS EVENTS (DELIVERED, READ, SENT)
        # =====================================================
        if value.get('statuses'):
            logger.info('WHATSAPP_STATUS_EVENT_IGNORED')
            return 'OK', 200

        # =====================================================
        # IGNORE NON-MESSAGE EVENTS
        # =====================================================
        if 'messages' not in value:
            logger.info('NON_MESSAGE_EVENT_IGNORED')
            return 'OK', 200

        # =====================================================
        # GET ACTUAL USER MESSAGE
        # =====================================================
        message = value['messages'][0]

        # Only process text messages
        if message.get('type') != 'text':
            logger.info(
                f'NON_TEXT_MESSAGE_IGNORED | type={message.get("type")}'
            )
            return 'OK', 200

        text = message.get('text', {}).get('body', '').strip()

        # Ignore empty text
        if not text:
            return 'OK', 200

        original_sender = message['from']
        logger.info(f'RAW_WHATSAPP_NUMBER = {original_sender}')
        sender = original_sender

        # Normalize Indian number for DB lookup
        normalized_sender = (
            sender[2:]
            if sender.startswith('91') and len(sender) == 12
            else sender
        )

        # =====================================================
        # RATE LIMITING CHECK
        # =====================================================
        if not check_rate_limit(normalized_sender):
            logger.warning(f'RATE_LIMIT_EXCEEDED | sender={original_sender}')
            send_text(
                original_sender,
                '⚠️ You are sending messages too fast. Please wait a moment and try again.'
            )
            return 'OK', 200

        logger.info(
            f'WHATSAPP_MESSAGE | sender={original_sender} | normalized={normalized_sender} | text={text[:50]}'
        )

        # Mark as read
        mark_read(message['id'])

        # =====================================================
        # AUTHENTICATE EMPLOYEE (USE FULL E.164 NUMBER)
        # =====================================================

        logger.info(f'DB_LOOKUP_INPUT = {sender}')

        # IMPORTANT: Use FULL WhatsApp number for database lookup
        employee = get_employee_by_whatsapp(sender)

        logger.info(f'DB_LOOKUP_OUTPUT = {employee}')

        # If employee not found
        if employee is None:
            logger.warning(
                f'UNREGISTERED_USER | sender={original_sender}'
            )

            send_text(
                original_sender,
                'You are not registered in the HR system.'
            )

            return 'OK', 200

        # Employee authenticated successfully
        logger.info(
            f'AUTH_SUCCESS | employee={employee["employee_id"]} | '
            f'name={employee["name"]}'
        )

        # Route message to HR bot
        router(employee, text)

    except Exception:
        logger.exception('WEBHOOK_ERROR')

    return 'OK', 200

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for monitoring and deployment platforms."""
    try:
        return {
            'status': 'healthy',
            'database': 'connected',
            'chroma': 'ready' if collection else 'not_ready',
            'collection_count': collection.count() if collection else 0,
            'gemini_client': 'initialized' if gemini_client else 'not_initialized',
            'service': 'whatsapp-hr-assistant'
        }, 200

    except Exception:
        logger.exception('HEALTH_CHECK_ERROR')
        return {
            'status': 'unhealthy'
        }, 500


@app.route('/videos/<path:filename>')
def serve_video(filename):
    """Serve uploaded videos for WhatsApp media delivery."""
    return send_from_directory(
        VIDEO_FOLDER,
        filename,
        as_attachment=False
    )


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == '__main__':
    initialize_database()

    init_gemini()

    build_index()

    init_chroma()   # MUST come AFTER build_index()

    logger.info('APPLICATION_STARTUP_COMPLETE')

    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False
    )


# cloudflared tunnel --url http://localhost:5000 

