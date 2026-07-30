import logging
import os
from upstash_redis import Redis

logger = logging.getLogger(__name__)

# Initialize Upstash Client using REST environment variables
try:
  redis_client = Redis(
      url=os.getenv("UPSTASH_REDIS_REST_URL", ""),
      token=os.getenv("UPSTASH_REDIS_REST_TOKEN", ""),
  )
except Exception as e:
  logger.error(f"UPSTASH_INIT_ERROR | {e}")
  redis_client = None


def get_chat_history(employee_id: str, max_messages: int = 4) -> str:
  """Retrieves the last N messages for an employee from Upstash Redis."""
  if not redis_client or not employee_id:
    return "None"

  try:
    key = f"chat_history:{employee_id}"
    # Retrieve the last `max_messages` items from list
    history_items = redis_client.lrange(key, -max_messages, -1)

    if not history_items:
      return "None"

    return "\n".join(history_items)
  except Exception as e:
    logger.warning(f"REDIS_GET_HISTORY_FAILED | user={employee_id} | {e}")
    return "None"


def add_to_chat_history(
    employee_id: str, query: str, answer: str, ttl_seconds: int = 1800
):
  """Appends user query & bot answer to Redis and resets 30-minute expiration."""
  if not redis_client or not employee_id:
    return

  try:
    key = f"chat_history:{employee_id}"

    # Push user message and bot response
    redis_client.rpush(key, f"User: {query}")
    redis_client.rpush(key, f"Assistant: {answer}")

    # Keep only the last 6 items to conserve storage
    redis_client.ltrim(key, -6, -1)

    # Set 30-minute auto-expiration (1800 seconds)
    redis_client.expire(key, ttl_seconds)
  except Exception as e:
    logger.warning(f"REDIS_SAVE_HISTORY_FAILED | user={employee_id} | {e}")