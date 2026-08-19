"""Standalone Windows RQ Worker Runner.

Location: run_worker.py
"""

import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from redis import Redis
from rq import Queue, SimpleWorker

# Explicitly import the task so it is registered in memory
from app.tasks.salary_tasks import process_bulk_salary_slips_job

REDIS_URL = os.getenv(
    "REDIS_URL",
    "rediss://default:gQAAAAAAAQizAAIgcDFhZjNhMWFmMWVhNGI0OTM4YTBjNjVhZTI3NTg2ZTY2Ng@solid-sparrow-67763.upstash.io:6379",
)

if __name__ == "__main__":
  redis_conn = Redis.from_url(REDIS_URL)
  queue = Queue("hr_tasks", connection=redis_conn)

  print("🚀 RQ Worker started on Windows. Listening on 'hr_tasks'...")
  worker = SimpleWorker([queue], connection=redis_conn)
  worker.work()