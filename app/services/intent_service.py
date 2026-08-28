"""
Intent Classification Service
Combines ultra-fast keyword matching with a Gemini LLM fallback.

Location: app/services/intent_service.py
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


def classify_intent(message: str, gemini_client=None) -> str:
    msg = message.lower().strip()

    # 1. GREETING & ONBOARDING (Instant match)
    greeting_patterns = [
        r"^hi\b",
        r"^hello\b",
        r"^hey\b",
        r"^start\b",
        r"^help\b",
        r"^good morning\b",
        r"^good afternoon\b",
        r"^good evening\b",
        r"^namaste\b",
    ]
    if any(re.search(pat, msg) for pat in greeting_patterns):
        return "greeting"

    # 2. INFORMATIONAL / POLICY QUESTIONS (Priority over action intents)
    # Catches questions like "What to apply for emergency leave", "How can I take leave", "Leave policy"
    rag_starters = (
        "what",
        "how",
        "why",
        "when",
        "can i",
        "is there",
        "tell me",
        "explain",
        "rules",
        "rule for",
        "policy",
        "process for",
        "guidelines",
        "procedure",
        "eligibility",
        "allowed",
        "criteria",
    )
    if any(msg.startswith(starter) for starter in rag_starters):
        return "rag"

    # Explicit policy keywords anywhere in the message
    rag_keywords = [
        "policy",
        "coverage",
        "covered",
        "insurance",
        "allowance",
        "claim",
        "gpa",
        "da",
        "limit",
        "notice period",
        "probation",
        "maternity",
        "paternity",
        "bereavement",
        "reimbursement",
    ]
    if any(k in msg for k in rag_keywords):
        return "rag"

    # 3. LEAVE BALANCE
    if any(
        k in msg
        for k in [
            "leave balance",
            "leaves balance",
            "leave status",
            "leave left",
            "leaves left",
            "leave remaining",
            "remaining leave",
            "how many leaves",
            "how many leave",
            "check balance",
            "available leave",
        ]
    ):
        return "leave_balance"

    # 4. LEAVE HISTORY
    if any(
        k in msg
        for k in [
            "leave history",
            "previous leaves",
            "past leaves",
            "leave records",
            "all leave requests",
            "my leaves",
        ]
    ):
        return "leave_history"

    # 5. SALARY SLIP
    if any(k in msg for k in ["salary slip", "payslip", "pay slip", "salary"]):
        return "salary_slip"

    # 6. TRAINING VIDEOS
    if any(
        k in msg
        for k in [
            "video",
            "watch video",
            "training video",
            "induction video",
            "health insurance video",
            "claim video",
            "health video",
            "orientation video",
            "onboarding video",
        ]
    ):
        return "training_video"

    # 7. LEAVE APPLICATION (Explicit operational action)
    leave_action_patterns = [
        r"\bapply\s+(for\s+)?leave\b",
        r"\bapplay\s+(for\s+)?leave\b",
        r"\bwant\s+(to\s+)?(take\s+)?leave\b",
        r"\bneed\s+(a\s+)?leave\b",
        r"\btake\s+(a\s+)?leave\b",
        r"\brequest\s+leave\b",
        r"\bbook\s+leave\b",
        r"\bgoing\s+on\s+leave\b",
        r"\bleave\s+from\b",
        r"\bsick\s+leave\s+today\b",
        r"\bon\s+leave\s+tomorrow\b",
    ]
    if any(re.search(pat, msg) for pat in leave_action_patterns):
        return "apply_leave"

    # 8. SMART FALLBACK VIA GEMINI API
    if gemini_client:
        try:
            prompt = f"""Classify the user message into exactly ONE intent.

Message: "{message}"

Intent Options:
- "greeting": Saying hi, hello, asking for general help.
- "rag": Informational or question about company policies, rules, benefits, allowances, limits, or how leave works.
- "apply_leave": The user wants to directly submit/book/apply for leave right now (e.g., "I need leave tomorrow", "Apply for 2 days leave").
- "leave_balance": Asking for remaining leave balance.
- "leave_history": Asking for previous/past leave records.
- "salary_slip": Asking for payslip or salary PDF.
- "training_video": Asking to watch video tutorials.

Return ONLY a JSON object: {{"intent": "one_of_the_intents_above"}}"""

            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.0},
            )
            result = json.loads(response.text.strip())
            return result.get("intent", "rag")
        except Exception as e:
            logger.warning(
                f"INTENT_FALLBACK_WARNING | Gemini fallback skipped: {e}"
            )

    # Default to RAG for safety
    return "rag"