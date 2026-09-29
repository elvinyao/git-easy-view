"""Real forked RQ worker, with explicit test-only crash/timing probes."""
import json
import os
from pathlib import Path
import signal
import time

from redis import Redis
from rq import Queue, Worker
from app.git_service import GitService

original = GitService.compare
def compare(self, source, target):
    if os.environ.get("GEV_PROBE_CRASH") == "1":
        os.kill(os.getpid(), signal.SIGKILL)
    start = time.monotonic()
    if os.environ.get("GEV_PROBE_DIRECTORY"):
        time.sleep(1)
    result = original(self, source, target)
    if directory := os.environ.get("GEV_PROBE_DIRECTORY"):
        Path(directory, f"{os.getpid()}.json").write_text(json.dumps([start, time.monotonic()]))
    return result

GitService.compare = compare
connection = Redis.from_url(os.environ["GEV_REDIS_URL"])
Worker([Queue(os.environ["GEV_QUEUE_NAME"], connection=connection)], connection=connection).work(burst=True)
