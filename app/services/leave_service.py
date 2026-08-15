from datetime import datetime
import json
import logging
import os
import re

from dateparser.search import search_dates
from flask import flash, redirect, send_file, url_for
from google.genai.errors import ClientError, ServerError

from app.services.whatsapp_service import send_interactive_buttons, send_text
from database import (
    apply_leave,
    can_approve_leave,
    get_connection,
    get_leave_history,
    log_activity,
)
from leave_session import leave_sessions
from validators import (
    ValidationError,
    validate_date_range,
    validate_leave_days,
)

logger = logging.getLogger(__name__)


def ask_leave_confirmation(
    sender: str, from_date: str, to_date: str, total_days: int, reason: str
):
  """Sends summary with Quick Reply confirmation buttons."""
  body_text = (
      f"📝 *Leave Request Summary*\n"
      f"━━━━━━━━━━━━━━━━━━━\n"
      f"📅 *From:* {from_date}\n"
      f"📅 *To:* {to_date}\n"
      f"⏳ *Total Duration:* {total_days} day(s)\n"
      f"📋 *Reason:* {reason}\n"
      f"━━━━━━━━━━━━━━━━━━━\n\n"
      f"Please confirm your submission below:"
  )

  buttons = [
      {"id": "btn_confirm_leave", "title": "Confirm ✅"},
      {"id": "btn_cancel_leave", "title": "Cancel ❌"},
  ]

  send_interactive_buttons(
      recipient_id=sender,
      body_text=body_text,
      buttons=buttons,
      header_text="🌴 Confirm Leave Application",
  )


def extract_leave_details(message: str) -> dict:
  """Extract leave details (dates and reason) from user message."""
  result = {
      "from_date": None,
      "to_date": None,
      "reason": None,
      "waiting_for": None,
  }

  msg = message.lower()

  # 1. Handle "today"
  if "today" in msg:
    result["from_date"] = datetime.now().strftime("%Y-%m-%d")

  # 2. Extract dates using dateparser
  dates = search_dates(
      message, settings={"PREFER_DATES_FROM": "future", "STRICT_PARSING": True}
  )

  if dates:
    parsed_dates = [d[1].strftime("%Y-%m-%d") for d in dates]

    if result["from_date"]:
      if len(parsed_dates) >= 1:
        result["to_date"] = parsed_dates[0]
    else:
      if len(parsed_dates) >= 2:
        result["from_date"] = parsed_dates[0]
        result["to_date"] = parsed_dates[1]
      elif len(parsed_dates) == 1:
        result["from_date"] = parsed_dates[0]

  # 3. Extract reason by stripping dates & conversational stop words
  clean = msg
  if dates:
    for original_text, _ in dates:
      clean = clean.replace(original_text.lower(), "")

  clean = clean.replace("today", "")

  remove_words = [
      "apply",
      "applay",
      "leave",
      "from",
      "to",
      "because",
      "for",
      "need",
      "i",
      "want",
      "on",
      "starting",
      "until",
      "please",
      "take",
      "request",
      "my",
      "me",
      "a",
      "the",
      "of",
  ]

  for word in remove_words:
    clean = re.sub(rf"\b{word}\b", "", clean, flags=re.IGNORECASE)

  clean = " ".join(clean.split()).strip()

  invalid_reasons = {"apply", "applay", "leave", "request", "take", ""}

  if clean and clean not in invalid_reasons:
    result["reason"] = clean
  else:
    result["reason"] = None

  # 4. Determine next expected field
  if not result["from_date"]:
    result["waiting_for"] = "from_date"
  elif not result["to_date"]:
    result["waiting_for"] = "to_date"
  elif not result["reason"]:
    result["waiting_for"] = "reason"
  else:
    result["waiting_for"] = "confirmation"

  return result


def detect_leave_type_rule_based(reason: str) -> tuple[str, str]:
  """Production-grade deterministic leave classifier. Returns: (leave_type, priority)"""
  reason = reason.lower()

  sick_keywords = [
      "fever",
      "cough",
      "cold",
      "flu",
      "headache",
      "migraine",
      "doctor",
      "hospital",
      "medical",
      "medicine",
      "sick",
      "ill",
      "infection",
      "vomit",
      "health",
      "surgery",
      "operation",
      "injury",
      "fracture",
      "pain",
      "checkup",
      "diagnosis",
      "blood test",
      "emergency",
  ]

  casual_keywords = [
      "family",
      "function",
      "wedding",
      "marriage",
      "festival",
      "ceremony",
      "personal",
      "relative",
      "birthday",
      "anniversary",
      "house",
      "travel",
      "trip",
      "vacation",
      "outing",
  ]

  critical_keywords = [
      "surgery",
      "operation",
      "icu",
      "emergency",
      "accident",
      "fracture",
      "hospitalized",
  ]

  if any(k in reason for k in critical_keywords):
    return "Sick Leave", "Critical"

  if any(k in reason for k in sick_keywords):
    return "Sick Leave", "High"

  if any(k in reason for k in casual_keywords):
    return "Casual Leave", "Normal"

  return "Earned Leave", "Normal"


def get_leave_policy(query=None):
  """Fetch leave policy using system RAG service safely."""
  try:
    from app.services.rag_service import hybrid_retrieve, math_rrf_rerank

    if not query:
      query = "leave policy sick casual earned maternity rules"

    dense, sparse, _ = hybrid_retrieve([query], top_k=3)
    context, _, _ = math_rrf_rerank(dense, sparse, top_k=2)
    return context
  except Exception as e:
    logger.warning(f"GET_LEAVE_POLICY_FALLBACK | error={e}")
    return (
        "Standard Leave Policy: Casual, Sick, and Earned Leaves subject to"
        " approval."
    )


def handle_leave_application(employee, leave_data, gemini_client):
  """Submits validated leave request to database."""
  sender = employee["whatsapp"]
  employee_id = employee["employee_id"]

  try:
    logger.info(f"LEAVE_APPLICATION_START | user={employee_id}")

    # Validate Required Fields
    required = ["from_date", "to_date", "reason"]
    missing = [f for f in required if not leave_data.get(f)]

    if missing:
      logger.warning(
          f"MISSING_LEAVE_DATA | user={employee_id} | missing={missing}"
      )
      send_text(sender, "❌ Missing leave information. Please try again.")
      return

    # Validate Dates
    from_date, to_date = validate_date_range(
        leave_data["from_date"], leave_data["to_date"]
    )
    leave_days = (to_date - from_date).days + 1

    logger.info(
        f"LEAVE_DAYS_CALCULATED | from={from_date} | to={to_date} |"
        f" days={leave_days}"
    )

    validate_leave_days(leave_days)

    # Classification
    reason = leave_data["reason"]
    leave_type, priority = detect_leave_type_rule_based(reason)

    # AI Validation for Ambiguous Cases
    if leave_type == "Earned Leave" and gemini_client is not None:
      try:
        policy = get_leave_policy(reason)
        prompt = f"""
Classify this leave request using the HR policy.
Reason: {reason}
Days: {leave_days}
Policy Context: {policy}

Return ONLY JSON:
{{"leave_type":"...","priority":"..."}}
"""
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={"temperature": 0.1, "max_output_tokens": 100},
        )

        text = response.text.strip()
        if text.startswith("```"):
          text = text.replace("```json", "").replace("```", "").strip()

        data = json.loads(text)
        if data.get("leave_type") in [
            "Sick Leave",
            "Casual Leave",
            "Earned Leave",
        ]:
          leave_type = data["leave_type"]
          priority = data.get("priority", priority)

      except (ServerError, ClientError):
        logger.warning(
            f"GEMINI_UNAVAILABLE | user={employee_id} | using_rule_based"
        )
      except Exception:
        logger.exception(f"AI_CLASSIFICATION_ERROR | user={employee_id}")

    # Check Balance
    allowed, balance_message = can_approve_leave(
        employee_id, leave_type, leave_days
    )

    if not allowed:
      logger.warning(
          f"INSUFFICIENT_BALANCE | user={employee_id} | type={leave_type}"
      )
      send_text(sender, f"❌ {balance_message}")
      return

    # Submit to Database
    apply_leave(
        employee_id,
        leave_data["from_date"],
        leave_data["to_date"],
        leave_days,
        reason,
        leave_type,
        priority,
    )

    log_activity(
        f"📝 {employee['name']} applied for {leave_type} ({leave_days} days)"
    )

    response_message = f"""✅ *Leave Request Submitted*

*Leave Type:* {leave_type}
*From:* {leave_data["from_date"]}
*To:* {leave_data["to_date"]}
*Total Days:* {leave_days}
*Reason:* {reason}
*Priority:* {priority}
*Status:* Pending Approval

Your request has been forwarded to your manager for review."""

    send_text(sender, response_message)

  except ValidationError as e:
    logger.warning(
        f"LEAVE_VALIDATION_ERROR | user={employee_id} | error={str(e)}"
    )
    send_text(sender, f"❌ {str(e)}")

  except Exception:
    logger.exception(f"LEAVE_APPLICATION_FATAL | user={employee_id}")
    send_text(
        sender,
        "❌ Unable to process your leave request right now. Please try again"
        " in a moment.",
    )


def start_leave_conversation(employee, message, gemini_client):
  """Start multi-turn leave application conversation."""
  sender = employee["whatsapp"]

  try:
    details = extract_leave_details(message)
    leave_sessions[sender] = details

    logger.info(f'START_LEAVE_CONVERSATION | user={employee["employee_id"]}')

    if not details["from_date"]:
      send_text(sender, "📅 From which date would you like to start your leave?")
      return

    if not details["to_date"]:
      send_text(
          sender,
          f"📅 Until which date do you need leave?\n\nYour leave starts on"
          f" {details['from_date']}.",
      )
      return

    if not details["reason"]:
      send_text(sender, "📝 Could you tell me the reason for your leave?")
      return

    # If all details were provided in the very first prompt, trigger confirmation buttons
    from_date, to_date = validate_date_range(
        details["from_date"], details["to_date"]
    )
    total_days = (to_date - from_date).days + 1
    details["total_days"] = total_days
    details["waiting_for"] = "confirmation"
    leave_sessions[sender] = details

    ask_leave_confirmation(
        sender=sender,
        from_date=str(details["from_date"]),
        to_date=str(details["to_date"]),
        total_days=total_days,
        reason=details["reason"],
    )

  except ValidationError as e:
    send_text(sender, f"❌ {str(e)}")
  except Exception as e:
    logger.exception(f'START_LEAVE_ERROR | user={employee["employee_id"]}')
    send_text(sender, "❌ Error processing your request. Please try again.")


def continue_leave_conversation(employee, message, gemini_client):
  """Continue multi-turn leave application conversation."""
  sender = employee["whatsapp"]
  employee_id = employee["employee_id"]

  try:
    session = leave_sessions.get(sender)

    if not session:
      logger.warning(f"NO_SESSION | user={employee_id}")
      send_text(sender, "❌ Session expired. Please start again.")
      return

    waiting = session.get("waiting_for")
    msg_cleaned = message.strip()
    msg_lower = msg_cleaned.lower()

    # 1. HANDLE CONFIRMATION BUTTON CLICKS & TEXT CONFIRMATIONS
    if waiting == "confirmation":
      if msg_cleaned == "btn_confirm_leave" or msg_lower in [
          "yes",
          "confirm",
          "ok",
          "y",
          "proceed",
      ]:
        handle_leave_application(employee, session, gemini_client)
        if sender in leave_sessions:
          del leave_sessions[sender]
        return

      elif msg_cleaned == "btn_cancel_leave" or msg_lower in [
          "no",
          "cancel",
          "stop",
          "exit",
      ]:
        if sender in leave_sessions:
          del leave_sessions[sender]
        send_text(sender, "❌ Leave request cancelled.")
        return

    details = extract_leave_details(message)

    # 2. SLOT-FILLING PROMPTS
    if waiting == "from_date":
      if details["from_date"]:
        session["from_date"] = details["from_date"]
        session["waiting_for"] = "to_date"
        leave_sessions[sender] = session
        send_text(sender, "📅 Great! Until which date do you need leave?")
        return
      else:
        send_text(
            sender,
            "❌ I couldn't understand the date. Please enter it in YYYY-MM-DD"
            " format (e.g., 2026-08-20).",
        )
        return

    elif waiting == "to_date":
      if details["from_date"] or details["to_date"]:
        session["to_date"] = details["from_date"] or details["to_date"]
        session["waiting_for"] = "reason"
        leave_sessions[sender] = session
        send_text(sender, "📝 What is the reason for your leave?")
        return
      else:
        send_text(
            sender,
            "❌ I couldn't understand the date. Please enter it again (e.g.,"
            " 2026-08-22).",
        )
        return

    elif waiting == "reason":
      if details["reason"]:
        session["reason"] = details["reason"]
      else:
        session["reason"] = message

      leave_sessions[sender] = session

    # 3. VERIFY ALL SLOTS AND TRIGGER CONFIRMATION BUTTONS
    if not session.get("from_date"):
      session["waiting_for"] = "from_date"
      leave_sessions[sender] = session
      send_text(sender, "📅 From which date would you like to start your leave?")
      return

    if not session.get("to_date"):
      session["waiting_for"] = "to_date"
      leave_sessions[sender] = session
      send_text(sender, "📅 Until which date do you need leave?")
      return

    if not session.get("reason"):
      session["waiting_for"] = "reason"
      leave_sessions[sender] = session
      send_text(sender, "📝 What is the reason for your leave?")
      return

    # All 3 slots collected -> calculate days and prompt confirmation buttons
    from_date, to_date = validate_date_range(
        session["from_date"], session["to_date"]
    )
    total_days = (to_date - from_date).days + 1
    session["total_days"] = total_days
    session["waiting_for"] = "confirmation"
    leave_sessions[sender] = session

    ask_leave_confirmation(
        sender=sender,
        from_date=str(session["from_date"]),
        to_date=str(session["to_date"]),
        total_days=total_days,
        reason=session["reason"],
    )

  except ValidationError as e:
    send_text(sender, f"❌ {str(e)}")
  except Exception as e:
    logger.exception(f"CONTINUE_LEAVE_ERROR | user={employee_id}")
    send_text(sender, "❌ An error occurred. Please start again.")
    if sender in leave_sessions:
      del leave_sessions[sender]


def handle_leave_balance(employee):
  """Send employee's leave balance to WhatsApp."""
  sender = employee["whatsapp"]
  employee_id = employee["employee_id"]

  conn = None
  try:
    reply = "📊 *Your Current Leave Balance*\n━━━━━━━━━━━━━━━━━━━\n\n"

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
            SELECT leave_name, yearly_limit 
            FROM leave_types 
            ORDER BY leave_name
        """)

    leave_types = cursor.fetchall()

    if not leave_types:
      logger.warning(f"NO_LEAVE_TYPES | user={employee_id}")
      send_text(sender, "❌ Leave types not configured in system.")
      return

    for leave_name, yearly_limit in leave_types:
      cursor.execute(
          """
                SELECT COALESCE(SUM(leave_days), 0)
                FROM leave_requests
                WHERE employee_id = %s
                  AND leave_type = %s
                  AND status = 'Approved'
            """,
          (employee_id, leave_name),
      )

      used = cursor.fetchone()[0]
      remaining = yearly_limit - used

      reply += f"🔹 *{leave_name}*\n"
      reply += f"• Total: {yearly_limit} days\n"
      reply += f"• Used: {used} days\n"
      reply += f"• Remaining: *{remaining} days*\n\n"

    send_text(sender, reply.strip())
    logger.info(f"LEAVE_BALANCE_SENT | user={employee_id}")

  except Exception as e:
    logger.exception(f"LEAVE_BALANCE_ERROR | user={employee_id}")
    send_text(sender, "❌ Error fetching leave balance.")
  finally:
    if conn:
      cursor.close()
      conn.close()


def handle_leave_history(employee):
  """Send employee's leave history to WhatsApp."""
  sender = employee["whatsapp"]
  employee_id = employee["employee_id"]

  try:
    history = get_leave_history(employee_id)

    if not history:
      send_text(sender, "📋 You haven't applied for any leave yet.")
      logger.info(f"NO_LEAVE_HISTORY | user={employee_id}")
      return

    message = "📋 *Your Leave History*\n━━━━━━━━━━━━━━━━━━━\n\n"

    for i, leave in enumerate(history, start=1):
      status_icon = (
          "✅"
          if leave[3] == "Approved"
          else ("⏳" if leave[3] == "Pending" else "❌")
      )
      message += (
          f"{i}. *{leave[2]}* {status_icon}\n"
          f"📅 *Dates:* {leave[0]} to {leave[1]}\n"
          f"📌 *Status:* {leave[3]}\n\n"
          f"───────────────────\n\n"
      )

    send_text(sender, message.strip())
    logger.info(f"LEAVE_HISTORY_SENT | user={employee_id}")

  except Exception as e:
    logger.exception(f"LEAVE_HISTORY_ERROR | user={employee_id}")
    send_text(sender, "❌ Error fetching leave history.")