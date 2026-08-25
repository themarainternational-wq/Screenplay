"""
core/jobstore.py
-----------
Minimal on-disk job persistence, so a long translate or text-to-speech
job can survive a ZeroGPU allocation ending, a dropped connection, the
daily free GPU quota running out mid-document, or the Space going to
sleep - without a paid database. Jobs are plain JSON files in a local
temp directory. This is "free/local temporary storage" as requested -
it does not persist across a full Space rebuild, only across ordinary
interruptions while the Space container is alive.
"""
import json
import os
import tempfile
import time
import uuid

JOBS_DIR = os.path.join(tempfile.gettempdir(), "screenwriter_assistant_jobs")
os.makedirs(JOBS_DIR, exist_ok=True)


def _path(job_id):
    # job_id is always our own uuid4 hex - safe to use directly in a path,
    # but guard anyway against anything unexpected reaching this function.
    safe = "".join(c for c in job_id if c.isalnum())
    return os.path.join(JOBS_DIR, f"{safe}.json")


def new_job_id():
    return uuid.uuid4().hex[:12]


def save(job_id, state):
    state["updated_at"] = time.time()
    path = _path(job_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)  # atomic on POSIX - never leaves a half-written file


def load(job_id):
    path = _path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def exists(job_id):
    return os.path.exists(_path(job_id))
