import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

workers = 1
threads = 4
timeout = 120
keepalive = 5
worker_class = "gthread"

max_requests = 100  # Automatically restart workers periodically to prevent memory leaks
max_requests_jitter = 10
accesslog = "-"
errorlog = "-"
loglevel = "info"