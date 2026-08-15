import json
import logging
import os
import re
from datetime import datetime

from dateparser.search import search_dates
from flask import flash, redirect, send_file, url_for
from google.genai.errors import ClientError, ServerError

from app.services.whatsapp_service import send_text
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


def format_display_date(date_val) -> str:
    """
    Formats date as 'DD-MM-YYYY' (e.g., 14-08-2026).
    Inserts a zero-width space (\u200b) between digits and hyphens to prevent
    WhatsApp from recognizing it as a clickable phone number or calendar dialer.
    """
    if not date_val:
        return "N/A"
    try:
        if isinstance(date_val, str):
            dt = datetime.strptime(date_val.strip(), "%Y-%m-%d")
        else:
            dt = date_val
        return dt.strftime("%d\u200b-%m\u200b-%Y")
    except Exception:
        return str(date_val)


def extract_leave_details(message):
    """Extract leave details from user message."""

    result = {
        "from_date": None,
        "to_date": None,
        "reason": None,
        "waiting_for": None,
    }

    msg = message.lower()

    # ---------------------------------
    # Handle "today"
    # ---------------------------------
    if "today" in msg:
        result["from_date"] = datetime.now().strftime("%Y-%m-%d")

    # ---------------------------------
    # Extract dates using dateparser
    # ---------------------------------
    dates = search_dates(
        message,
        settings={"PREFER_DATES_FROM": "future", "STRICT_PARSING": True},
    )

    if dates:
        parsed_dates = [d[1].strftime("%Y-%m-%d") for d in dates]

        # If "today" already set from_date, use detected date as to_date
        if result["from_date"]:
            if len(parsed_dates) >= 1:
                result["to_date"] = parsed_dates[0]
        else:
            if len(parsed_dates) >= 2:
                result["from_date"] = parsed_dates[0]
                result["to_date"] = parsed_dates[1]
            elif len(parsed_dates) == 1:
                result["from_date"] = parsed_dates[0]

    # ---------------------------------
    # Extract reason by removing keywords
    # ---------------------------------
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

    # ---------------------------------
    # Determine what information is missing
    # ---------------------------------
    if not result["from_date"]:
        result["waiting_for"] = "from_date"
    elif not result["to_date"]:
        result["waiting_for"] = "to_date"
    elif not result["reason"]:
        result["waiting_for"] = "reason"
    else:
        result["waiting_for"] = None

    return result


def detect_leave_type_rule_based(reason: str) -> tuple[str, str]:
    """Production-grade deterministic leave classifier.

    Returns: (leave_type, priority)
    """

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
        return "Standard Leave Policy: Casual, Sick, and Earned Leaves subject to approval."


def handle_leave_application(employee, leave_data, gemini_client):
    """Production-ready leave application handler."""

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

        logger.info(
            f"RULE_BASED_CLASSIFICATION | user={employee_id} |"
            f" type={leave_type} | priority={priority}"
        )

        # AI Validation for Ambiguous Cases
        ai_used = False
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
                    ai_used = True

            except (ServerError, ClientError):
                logger.warning(
                    f"GEMINI_UNAVAILABLE | user={employee_id} | using_rule_based"
                )
            except Exception:
                logger.exception(f"AI_CLASSIFICATION_ERROR | user={employee_id}")

        logger.info(
            f"FINAL_LEAVE_CLASSIFICATION | user={employee_id} | type={leave_type} |"
            f" ai_used={ai_used}"
        )

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

        logger.info(
            f"LEAVE_APPLIED | user={employee_id} | type={leave_type} |"
            f" days={leave_days}"
        )

        # Format dates with zero-width space protection for WhatsApp output
        formatted_from = format_display_date(leave_data["from_date"])
        formatted_to = format_display_date(leave_data["to_date"])

        response_message = f"""✅ *Leave Request Submitted*

*Leave Type:* {leave_type}
*From:* {formatted_from}
*To:* {formatted_to}
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
            formatted_from = format_display_date(details["from_date"])
            send_text(
                sender,
                f"📅 Until which date do you need leave?\n\nYour leave starts on"
                f" {formatted_from}.",
            )
            return

        if not details["reason"]:
            send_text(sender, "📝 Could you tell me the reason for your leave?")
            return

        handle_leave_application(employee, details, gemini_client)
        del leave_sessions[sender]

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
        details = extract_leave_details(message)

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
                    "❌ I couldn't understand the date. Please enter it again (e.g.,"
                    " 14-08-2026)",
                )
                return

        elif waiting == "to_date":
            if details["from_date"]:
                session["to_date"] = details["from_date"]
                session["waiting_for"] = "reason"
                leave_sessions[sender] = session
                send_text(sender, "📝 What is the reason for your leave?")
                return
            else:
                send_text(
                    sender, "❌ I couldn't understand the date. Please enter it again."
                )
                return

        elif waiting == "reason":
            if details["reason"]:
                session["reason"] = details["reason"]
            else:
                session["reason"] = message

            leave_sessions[sender] = session

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

        handle_leave_application(employee, session, gemini_client)
        if sender in leave_sessions:
            del leave_sessions[sender]
        logger.info(f"LEAVE_SUBMITTED | user={employee_id}")

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
        reply = "*Your Leave Balance*\n\n"

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

            reply += f"*{leave_name}*\n"
            reply += f"Total: {yearly_limit} days\n"
            reply += f"Used: {used} days\n"
            reply += f"Remaining: {remaining} days\n\n"

        send_text(sender, reply)
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

        message = "📋 *Your Leave History*\n\n"

        for i, leave in enumerate(history, start=1):
            formatted_from = format_display_date(leave[0])
            formatted_to = format_display_date(leave[1])

            message += (
                f"{i}. \n"
                f"From: {formatted_from}\n"
                f"To: {formatted_to}\n"
                f"Type: {leave[2]}\n"
                f"Status: {leave[3]}\n\n"
                + "-" * 30
                + "\n\n"
            )

        send_text(sender, message)
        logger.info(f"LEAVE_HISTORY_SENT | user={employee_id}")

    except Exception as e:
        logger.exception(f"LEAVE_HISTORY_ERROR | user={employee_id}")
        send_text(sender, "❌ Error fetching leave history.")