import os
from dotenv import load_dotenv
from redis import Redis

load_dotenv()

r = Redis.from_url(
    os.getenv("REDIS_URL"),
    decode_responses=True
)

# Delete only cache, policy, and chat-related keys
keys_to_delete = [
    k for k in r.keys("*")
    if "cache" in k.lower()
    or "policy" in k.lower()
    or "chat" in k.lower()
]

if keys_to_delete:
    deleted = r.delete(*keys_to_delete)
    print(f"✅ Cleared {deleted} keys successfully!")
else:
    print("ℹ️ No matching keys found.")