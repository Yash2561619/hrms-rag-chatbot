"""Chat Memory and Conversation History Service using Upstash Redis.
Location: app/services/memory_service.py
"""

import logging
import os
from typing import Optional
from upstash_redis import Redis

logger = logging.getLogger(__name__)

_redis_client: Optional[Redis] = None


def get_redis_client() -> Optional[Redis]:
    """Lazily initializes and returns the Upstash Redis client."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    redis_url = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("REDIS_URL")
    redis_token = os.getenv("UPSTASH_REDIS_REST_TOKEN")

    if redis_url and redis_token:
        try:
            _redis_client = Redis(url=redis_url, token=redis_token)
            logger.info("UPSTASH_MEMORY_CLIENT_INITIALIZED ✅")
        except Exception as e:
            logger.error(f"UPSTASH_MEMORY_INIT_FAILED | {e}")
            _redis_client = None
    else:
        logger.warning("UPSTASH_MEMORY_ENV_VARS_MISSING | Chat history disabled")
        _redis_client = None

    return _redis_client


def get_chat_history(employee_id: str, max_messages: int = 4) -> str:
    """Retrieves the last N messages for an employee from Upstash Redis."""
    client = get_redis_client()
    if not client or not employee_id:
        return "None"

    try:
        key = f"chat_history:{employee_id}"
        history_items = client.lrange(key, -max_messages, -1)

        if not history_items:
            return "None"

        # Ensure all items are parsed cleanly as strings
        formatted_items = [str(item).strip() for item in history_items if item]
        return "\n".join(formatted_items) if formatted_items else "None"
    except Exception as e:
        logger.warning(f"REDIS_GET_HISTORY_FAILED | user={employee_id} | {e}")
        return "None"


def add_to_chat_history(
    employee_id: str, query: str, answer: str, ttl_seconds: int = 1800
):
    """Appends user query & bot answer to Redis and resets 30-minute expiration."""
    client = get_redis_client()
    if not client or not employee_id:
        return

    try:
        key = f"chat_history:{employee_id}"
        clean_query = query.strip().replace("\n", " ")
        clean_answer = answer.strip().replace("\n", " ")

        # Append user message and bot response
        client.rpush(key, f"User: {clean_query}")
        client.rpush(key, f"Assistant: {clean_answer}")

        # Keep only the last 6 messages to conserve memory
        client.ltrim(key, -6, -1)

        # Reset 30-minute auto-expiration
        client.expire(key, ttl_seconds)
    except Exception as e:
        logger.warning(f"REDIS_SAVE_HISTORY_FAILED | user={employee_id} | {e}")