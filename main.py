"""
WhatsApp HR Assistant - Main Flask Application (OPTIMIZED FOR RENDER)
Webhook server with Gemini API embeddings to fit in 512 MB RAM and avoid ONNX crashes.
"""


import os
import sys

# MUST BE AT THE VERY TOP: Force ChromaDB & ONNX to skip native SIMD optimizations
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMADB_DISABLE_TELEMETRY"] = "true"



from types import ModuleType

# Create a mock onnxruntime module to prevent ChromaDB from loading native C++ binaries that crash on Render
class DummyONNX(ModuleType):
    def __getattr__(self, name):
        return None

sys.modules['onnxruntime'] = DummyONNX('onnxruntime')
# Add project root to Python path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import logging
import traceback
import chromadb
from flask import Flask, request, send_from_directory
import google.genai as genai

from app.routes.admin_routes import admin_bp
from app.services.intent_service import classify_intent
from app.services.leave_service import (
    continue_leave_conversation,
    handle_leave_balance,
    handle_leave_history,
    start_leave_conversation,
)
from app.services.media_service import (
    handle_salary_slip,
    handle_training_video,
)
from app.services.rag_service import handle_rag_query
from app.services.whatsapp_service import configure, mark_read, send_text
from app.services.lazy_embedding import LazyEmbeddingFunction
from config import Config
from database import get_employee_by_whatsapp, initialize_database
from leave_session import leave_sessions
from rate_limiter import check_rate_limit

# Setup logging
LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(
            os.path.join(LOG_DIR, 'hr_chatbot.log'), encoding='utf-8'
        ),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)

# Initialize Flask App
app = Flask(__name__)
app.config.from_object(Config)

app.secret_key = app.config.get('SECRET_KEY', 'apexhr-super-secret-key-2026')
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_TYPE'] = 'filesystem'

POLICY_FOLDER = app.config['POLICY_FOLDER']
SALARY_FOLDER = app.config['SALARY_FOLDER']
VIDEO_FOLDER = app.config['VIDEO_FOLDER']

os.makedirs(POLICY_FOLDER, exist_ok=True)
os.makedirs(SALARY_FOLDER, exist_ok=True)
os.makedirs(VIDEO_FOLDER, exist_ok=True)

app.register_blueprint(admin_bp)

GEMINI_API_KEY = app.config.get('GEMINI_API_KEY')
WHATSAPP_TOKEN = app.config.get('WHATSAPP_TOKEN')
PHONE_NUMBER_ID = app.config.get('PHONE_NUMBER_ID')
WABA_ID = app.config.get('WABA_ID')
VERIFY_TOKEN = app.config.get('VERIFY_TOKEN')

logger.info("=" * 50)
logger.info("CONFIG DEBUG")
logger.info(f"WHATSAPP_TOKEN exists: {bool(WHATSAPP_TOKEN)}")
logger.info(f"PHONE_NUMBER_ID: {PHONE_NUMBER_ID}")
logger.info(f"WABA_ID: {WABA_ID}")
logger.info(f"VERIFY_TOKEN exists: {bool(VERIFY_TOKEN)}")
logger.info("=" * 50)

configure(WHATSAPP_TOKEN, PHONE_NUMBER_ID)

logger.info('Configuration loaded successfully')

collection = None
gemini_client = None


def init_gemini():
    """Initialize Gemini client at startup."""
    global gemini_client
    logger.info("[STARTUP] Initializing Gemini client...")

    if GEMINI_API_KEY:
        try:
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            logger.info('[STARTUP] ✅ Gemini client initialized')
        except Exception as e:
            logger.error(f'[STARTUP] ❌ Gemini initialization failed: {e}')
            logger.error(traceback.format_exc())
    else:
        logger.error('[STARTUP] ❌ GEMINI_API_KEY not set')


from chromadb.config import Settings

def init_chroma():
    global collection
    logger.info("[STARTUP] Initializing ChromaDB safely...")
    try:
        client = chromadb.EphemeralClient()
        embedding_fn = LazyEmbeddingFunction(model_name="text-embedding-004")
        
        collection = client.get_or_create_collection(
            name="hr_policies",
            embedding_function=embedding_fn
        )
        logger.info("[STARTUP] ✅ ChromaDB initialized successfully!")
    except Exception as e:
        logger.error(f"[STARTUP] ❌ ChromaDB initialization failed: {e}")

def router(employee, message):
    intent = classify_intent(message)
    sender = employee["whatsapp"]

    logger.info(f"Intent classified: {intent}")

    try:
        if sender in leave_sessions:
            msg = message.lower().strip()

            if msg in [
                "cancel",
                "stop",
                "exit",
                "quit",
                "never mind",
                "forget it",
            ]:
                del leave_sessions[sender]
                send_text(sender, "❌ Leave request cancelled.")
                return

            if msg in ["restart", "reset", "start over"]:
                leave_sessions[sender] = {
                    "from_date": None,
                    "to_date": None,
                    "reason": None,
                    "waiting_for": "from_date",
                }

                send_text(
                    sender,
                    "🔄 Let's start again.\n\n📅 From which date would you like to start your leave?",
                )
                return

            continue_leave_conversation(employee, message, gemini_client)
            return

        if intent == "rag":
            handle_rag_query(
                employee,
                message,
                collection,
                gemini_client,
            )

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
        logger.exception(f'ROUTER_ERROR | user={employee.get("employee_id")}')
        send_text(
            sender, '❌ Something went wrong while processing your request.'
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

        entry = body.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})

        if value.get('statuses'):
            logger.info('WHATSAPP_STATUS_EVENT_IGNORED')
            return 'OK', 200

        if 'messages' not in value:
            logger.info('NON_MESSAGE_EVENT_IGNORED')
            return 'OK', 200

        message = value['messages'][0]

        if message.get('type') != 'text':
            logger.info(
                f'NON_TEXT_MESSAGE_IGNORED | type={message.get("type")}'
            )
            return 'OK', 200

        text = message.get('text', {}).get('body', '').strip()
        if not text:
            return 'OK', 200

        original_sender = message['from']
        logger.info(f'RAW_WHATSAPP_NUMBER = {original_sender}')
        sender = original_sender

        normalized_sender = (
            sender[2:]
            if sender.startswith('91') and len(sender) == 12
            else sender
        )

        if not check_rate_limit(normalized_sender):
            logger.warning(f'RATE_LIMIT_EXCEEDED | sender={original_sender}')
            send_text(
                original_sender,
                '⚠️ You are sending messages too fast. Please wait a moment and try again.',
            )
            return 'OK', 200

        logger.info(
            f'WHATSAPP_MESSAGE | sender={original_sender} | normalized={normalized_sender} | text={text[:50]}'
        )

        mark_read(message['id'])

        employee = get_employee_by_whatsapp(sender)

        if employee is None:
            logger.warning(f'UNREGISTERED_USER | sender={original_sender}')
            send_text(
                original_sender, 'You are not registered in the HR system.'
            )
            return 'OK', 200

        logger.info(
            f'AUTH_SUCCESS | employee={employee.get("employee_id")} | name={employee.get("name")}'
        )

        router(employee, text)

    except Exception:
        logger.exception('WEBHOOK_ERROR')

    return 'OK', 200


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for deployment platforms like Render."""
    try:
        doc_count = collection.count() if collection else 0
    except Exception:
        doc_count = "error_fetching_count"

    return {
        'status': 'healthy',
        'database': 'connected',
        'chroma': 'ready' if collection else 'not_ready',
        'collection_count': doc_count,
        'gemini_client': (
            'initialized' if gemini_client else 'not_initialized'
        ),
        'service': 'whatsapp-hr-assistant',
    }, 200


@app.route('/videos/<path:filename>')
def serve_video(filename):
    """Serve uploaded videos for WhatsApp media delivery."""
    return send_from_directory(
        VIDEO_FOLDER, filename, as_attachment=False
    )


# ============================================================================
# STARTUP SEQUENCE
# ============================================================================
_initialized = False

@app.before_request
def startup_initialization():
    """Run database, Gemini, and Chroma initializations once before handling the first HTTP request.
    This allows Gunicorn to bind to Render's port instantly on startup.
    """
    global _initialized
    if not _initialized:
        logger.info("=" * 80)
        logger.info("STARTING WhatsApp HR Assistant (Background Startup)")
        logger.info("=" * 80)

        logger.info("[STARTUP] Initializing database...")
        try:
            initialize_database()
            logger.info("[STARTUP] ✅ Database initialized")
        except Exception as e:
            logger.error(f"[STARTUP] ❌ Database init error: {e}")

        logger.info("[STARTUP] Step 1: Initializing Gemini...")
        init_gemini()

        logger.info("[STARTUP] Step 2: Initializing Chroma...")
        init_chroma()

        logger.info("=" * 80)
        logger.info("APPLICATION_STARTUP_COMPLETE ✅")
        logger.info(f"Collection status: {collection}")
        logger.info("=" * 80)

        _initialized = True


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)