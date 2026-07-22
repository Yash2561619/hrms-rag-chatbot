import time

user_requests = {}

RATE_LIMIT = 10      # max requests
TIME_WINDOW = 60     # per 60 seconds

def check_rate_limit(user_id: str):
    now = time.time()

    if user_id not in user_requests:
        user_requests[user_id] = []

    # Keep only recent requests
    user_requests[user_id] = [
        t for t in user_requests[user_id]
        if now - t < TIME_WINDOW
    ]

    if len(user_requests[user_id]) >= RATE_LIMIT:
        wait_time = int(
            TIME_WINDOW - (now - user_requests[user_id][0])
        )
        return False, wait_time

    user_requests[user_id].append(now)
    return True, 0