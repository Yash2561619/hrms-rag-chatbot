"""Script to completely wipe Redis Semantic Cache and Chat History.
Location: clear_all_cache.py
"""

import os
import sys
from dotenv import load_dotenv
from upstash_redis import Redis

# Ensure project root is loaded
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv()

REDIS_URL = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("REDIS_URL")
REDIS_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

if not REDIS_URL or not REDIS_TOKEN:
    print("❌ ERROR: UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN missing in .env")
    sys.exit(1)

try:
    client = Redis(url=REDIS_URL, token=REDIS_TOKEN)
    print("🔌 Connected to Upstash Redis...")

    # 1. Delete the primary RAG Semantic Cache index
    semantic_key = "semantic_rag_cache_index"
    deleted_semantic = client.delete(semantic_key)
    print(f"🗑️  Deleted Semantic Cache Index ('{semantic_key}'): {deleted_semantic > 0}")

    # 2. Find and delete all conversation memory keys (chat_history:*)
    keys = client.keys("chat_history:*")
    if keys:
        deleted_history_count = 0
        for k in keys:
            client.delete(k)
            deleted_history_count += 1
        print(f"🗑️  Deleted {deleted_history_count} active employee chat history sessions.")
    else:
        print("ℹ️  No active chat history keys found.")

    # 3. Optional: Flush entire Redis database if dedicated to this bot
    # client.flushdb()

    print("\n✅ SUCCESS: All cache has been cleared! The system is ready to store fresh cache entries.")

except Exception as e:
    print(f"❌ FAILED to clear cache: {e}")