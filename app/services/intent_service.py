"""
Intent Classification Service
Combines ultra-fast keyword matching with a Gemini LLM fallback.

Location: app/services/intent_service.py
"""

import json
import logging

logger = logging.getLogger(__name__)


import json
import logging
import re

logger = logging.getLogger(__name__)


def classify_intent(message: str, gemini_client=None) -> str:
    msg = message.lower().strip()

    # 1. GREETING & ONBOARDING (Checked first for clean routing)
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

    # 2. LEAVE BALANCE
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
        ]
    ):
        return "leave_balance"

    # 3. LEAVE HISTORY
    if any(
        k in msg
        for k in [
            "leave history",
            "previous leaves",
            "past leaves",
            "leave records",
            "all leave requests",
        ]
    ):
        return "leave_history"

    # 4. SALARY SLIP
    if any(k in msg for k in ["salary slip", "payslip", "pay slip", "salary"]):
        return "salary_slip"

    # 5. TRAINING VIDEOS
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

    # 6. LEAVE APPLICATION
    if "leave" in msg and any(
        word in msg
        for word in [
            "apply",
            "applay",  # common typo
            "want",
            "need",
            "take",
            "request",
        ]
    ):
        return "apply_leave"

    # 7. EXPLICIT RAG OVERRIDES (Policy, Coverage, Rules, Allowance)
    if any(
        k in msg
        for k in [
            "policy",
            "coverage",
            "covered",
            "insurance",
            "allowance",
            "claim",
            "gpa",
            "da",
            "rule",
            "limit",
        ]
    ):
        return "rag"

    # 8. SMART FALLBACK via Gemini API
    if gemini_client:
        try:
            prompt = f"""Classify the intent of this employee message: "{message}"

Intents:
- "greeting": Saying hi, hello, asking for help, or starting a conversation.
- "rag": Policy questions, benefits, insurance limits, daily allowance, HR rules.
- "apply_leave": Requesting or taking leave.
- "leave_balance": Inquiring about remaining leave days.
- "salary_slip": Requesting payslips or salary details.
- "training_video": Explicitly asking to watch video tutorials.
- "leave_history": Checking past leave applications.

Return ONLY JSON: {{"intent": "one_of_the_intents_above"}}"""

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

    # Default to RAG if no rules matched
    return "rag"