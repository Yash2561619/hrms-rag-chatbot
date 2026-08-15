"""WhatsApp HR Assistant - Main Flask Application (OPTIMIZED WITH FAISS + S3 & POSTGRESQL)

Webhook server using FAISS, Neon PostgreSQL, and Gemini API for ultra-fast RAG on Render.
"""

import logging
import os
import sys
import threading
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
  sys.path.insert(0, PROJECT_ROOT)

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
from app.services.s3_service import sync_faiss_from_s3
from app.services.whatsapp_service import configure, mark_read, send_text
from config import Config
from database import get_employee_by_whatsapp, initialize_database
from leave_session import leave_sessions
from rate_limiter import check_rate_limit

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            os.path.join(LOG_DIR, "hr_chatbot.log"), encoding="utf-8"
        ),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)
_initialized = False

app.secret_key = app.config.get("SECRET_KEY", "apexhr-super-secret-key-2026")
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"

POLICY_FOLDER = app.config["POLICY_FOLDER"]
SALARY_FOLDER = app.config["SALARY_FOLDER"]
VIDEO_FOLDER = app.config["VIDEO_FOLDER"]

os.makedirs(POLICY_FOLDER, exist_ok=True)
os.makedirs(SALARY_FOLDER, exist_ok=True)
os.makedirs(VIDEO_FOLDER, exist_ok=True)

app.register_blueprint(admin_bp)

GEMINI_API_KEY = app.config.get("GEMINI_API_KEY")
WHATSAPP_TOKEN = app.config.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = app.config.get("PHONE_NUMBER_ID")
WABA_ID = app.config.get("WABA_ID")
VERIFY_TOKEN = app.config.get("VERIFY_TOKEN")

configure(WHATSAPP_TOKEN, PHONE_NUMBER_ID)

# Global Gemini client variable
gemini_client = None


def init_gemini():
  """Initialize Gemini client."""
  global gemini_client
  if GEMINI_API_KEY:
    try:
      gemini_client = genai.Client(api_key=GEMINI_API_KEY)
      logger.info("[STARTUP] ✅ Gemini client initialized")
    except Exception as e:
      logger.error(f"[STARTUP] ❌ Gemini initialization failed: {e}")


def startup_background_tasks():
  """Runs once on application startup."""
  global _initialized
  if _initialized:
    return

  logger.info("=" * 50)
  logger.info("STARTING APP INITIALIZATION")
  logger.info("=" * 50)

  # 1. Initialize PostgreSQL Database Tables
  try:
    initialize_database()
    logger.info("[STARTUP] ✅ PostgreSQL database initialized")
  except Exception as e:
    logger.error(f"[STARTUP] PostgreSQL DB init error: {e}")

  # 2. Pull latest FAISS index from S3
  try:
    sync_faiss_from_s3()
    logger.info("[STARTUP] ✅ FAISS index synced from S3")
  except Exception as e:
    logger.error(f"[STARTUP] S3 sync error: {e}")

  # 3. Pre-warm FAISS and BM25 indexes in memory
  try:
    from app.services.rag_service import load_indexes

    load_indexes()
    logger.info("[STARTUP] ✅ Indexes pre-warmed successfully")
  except Exception as e:
    logger.error(f"[STARTUP] Index pre-warm error: {e}")

  # 4. Initialize Gemini Client
  try:
    init_gemini()
  except Exception as e:
    logger.error(f"[STARTUP] Gemini init error: {e}")

  _initialized = True


# Run initialization safely on module load
startup_background_tasks()


def router(employee, message):
  """Route incoming message based on intent."""
  global gemini_client

  if gemini_client is None:
    init_gemini()

  intent = classify_intent(message, gemini_client)
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
            (
                "🔄 Let's start again.\n\n📅 From which date would you like to"
                " start your leave?"
            ),
        )
        return

      continue_leave_conversation(employee, message, gemini_client)
      return

    if intent == "greeting":
      handle_greeting(employee)

    elif intent == "rag":
      handle_rag_query(
          employee,
          message,
          None,  # FAISS handles vector loading internally in rag_service
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
        sender, "❌ Something went wrong while processing your request."
    )


@app.route("/webhook", methods=["GET"])
def verify():
  if (
      request.args.get("hub.mode") == "subscribe"
      and request.args.get("hub.verify_token") == VERIFY_TOKEN
  ):
    logger.info("WEBHOOK_VERIFIED")
    return request.args.get("hub.challenge"), 200

  logger.warning("WEBHOOK_VERIFICATION_FAILED")
  return "Forbidden", 403


PROCESSED_MESSAGE_IDS = {}


def is_duplicate_message(message_id: str) -> bool:
  """Checks if WhatsApp webhook message ID was already processed in the last 60 seconds."""
  if not message_id:
    return False

  now = time.time()

  # Clean up old message IDs (> 60 seconds old)
  expired_keys = [k for k, v in PROCESSED_MESSAGE_IDS.items() if now - v > 60]
  for k in expired_keys:
    del PROCESSED_MESSAGE_IDS[k]

  if message_id in PROCESSED_MESSAGE_IDS:
    return True

  PROCESSED_MESSAGE_IDS[message_id] = now
  return False


@app.route("/webhook", methods=["POST"])
def webhook():
  try:
    body = request.get_json(silent=True) or {}

    entry = body.get("entry", [{}])[0]
    changes = entry.get("changes", [{}])[0]
    value = changes.get("value", {})

    if value.get("statuses"):
      logger.info("WHATSAPP_STATUS_EVENT_IGNORED")
      return "OK", 200

    if "messages" not in value:
      logger.info("NON_MESSAGE_EVENT_IGNORED")
      return "OK", 200

    message = value["messages"][0]
    message_id = message.get("id")

    # 1. STEP 1: Deduplication Guard
    if is_duplicate_message(message_id):
      logger.info(f"DUPLICATE_WEBHOOK_SKIPPED | message_id={message_id}")
      return "OK", 200

    # 2. STEP 2: Filter non-text messages
    if message.get("type") != "text":
      logger.info(f'NON_TEXT_MESSAGE_IGNORED | type={message.get("type")}')
      return "OK", 200

    text = message.get("text", {}).get("body", "").strip()
    if not text:
      return "OK", 200

    original_sender = message["from"]
    logger.info(f"RAW_WHATSAPP_NUMBER = {original_sender}")
    sender = original_sender

    normalized_sender = (
        sender[2:] if sender.startswith("91") and len(sender) == 12 else sender
    )

    # 3. STEP 3: Rate limiting check
    if not check_rate_limit(normalized_sender):
      logger.warning(f"RATE_LIMIT_EXCEEDED | sender={original_sender}")
      send_text(
          original_sender,
          (
              "⚠️ You are sending messages too fast. Please wait a moment and"
              " try again."
          ),
      )
      return "OK", 200

    logger.info(
        f"WHATSAPP_MESSAGE | sender={original_sender} |"
        f" normalized={normalized_sender} | text={text[:50]}"
    )

    mark_read(message["id"])

    employee = get_employee_by_whatsapp(sender)

    if employee is None:
      logger.warning(f"UNREGISTERED_USER | sender={original_sender}")
      send_text(original_sender, "You are not registered in the HR system.")
      return "OK", 200

    logger.info(
        f"AUTH_SUCCESS | employee={employee.get('employee_id')} |"
        f" name={employee.get('name')}"
    )

    # 4. STEP 4: Process query in background thread
    thread = threading.Thread(
        target=router, args=(employee, text), daemon=True
    )
    thread.start()

  except Exception:
    logger.exception("WEBHOOK_ERROR")

  # Return HTTP 200 immediately to Meta
  return "OK", 200


@app.route("/health", methods=["GET"])
def health():
  """Health check endpoint for Render."""
  faiss_exists = os.path.exists("faiss_index")

  return {
      "status": "healthy",
      "database": "postgresql_connected",
      "faiss_index": "ready" if faiss_exists else "not_ready",
      "gemini_client": (
          "initialized" if gemini_client else "not_initialized"
      ),
      "service": "whatsapp-hr-assistant",
  }, 200


@app.route("/videos/<path:filename>")
def serve_video(filename):
  """Serve uploaded videos for WhatsApp media delivery."""
  return send_from_directory(VIDEO_FOLDER, filename, as_attachment=False)


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port, debug=False)