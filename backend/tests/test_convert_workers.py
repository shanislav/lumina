"""Workers (convert/workers.py): pairing, which jobs a worker gets, its lease, giving a job back, a plan it made."""

import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.core import registry
from app.db import DB_PATH, init_db
from app.modules.convert import jobs, workers


async def _worker(encoders=("libx265",)) -> dict:
    await init_db(registry.discover())
    added = await workers.add_worker(workers.NewWorker(name="Notebook"))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE convert_workers SET info = ? WHERE id = ?", (json.dumps({"encoders": list(encoders)}), added["id"]))
        conn.commit()
        return {**dict(conn.execute("SELECT * FROM convert_workers WHERE id = ?", (added["id"],)).fetchone()),
                "token": added["token"]}


async def test_the_token_is_shown_once_and_kept_only_as_its_hash():
    w = await _worker()
    assert w["token"].startswith("lw_") and w["token_hash"] == workers._hash(w["token"]) and w["token"] not in w["token_hash"]


async def test_a_worker_gets_only_jobs_meant_for_workers_its_encoders_can_do(monkeypatch):
    monkeypatch.setattr(jobs._wake, "set", lambda: None)
    w = await _worker(encoders=("libx265",))
    await jobs.enqueue("plan", ["/m/server.mkv"], options={"where": "server"})
    await jobs.enqueue("plan", ["/m/gpu.mkv"], options={"where": "worker", "speed": "gpu"})
    await jobs.enqueue("dv", ["/m/dv.mkv"], options={"where": "worker"})          # DV remux: the server's
    await jobs.enqueue("plan", ["/m/pc.mkv"], options={"where": "worker"})
    got = await workers._claim(w)
    assert got["path"] == "/m/pc.mkv" and got["work"].endswith("/.lumina-convert/w%d" % got["id"])
    assert await workers._claim(w) is None                         # nothing else fits a CPU-only worker
    job = await jobs.get(got["id"])
    assert job["status"] == "running" and job["worker_id"] == w["id"] and job["lease_until"]
    # the server's own loop does not take a worker's job, takes its own
    server = await jobs._next()
    assert server["path"] == "/m/server.mkv"


async def test_a_silent_worker_loses_its_job_and_a_closed_one_gives_it_back(monkeypatch):
    monkeypatch.setattr(jobs._wake, "set", lambda: None)
    w = await _worker()
    await jobs.enqueue("plan", ["/m/a.mkv"], options={"where": "any"})
    got = await workers._claim(w)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE convert_jobs SET lease_until = '2000-01-01 00:00:00' WHERE id = ?", (got["id"],))
    assert await jobs.release_expired() == 1
    assert (await jobs.get(got["id"]))["status"] == "queued"
    again = await workers._claim(w)
    await workers.progress(again["id"], workers.Progress(phase="sample", value=1), worker=w)
    assert (await jobs.get(again["id"]))["progress"]["phase"] == "sample"
    await workers.fail(again["id"], workers.Failed(give_back=True), worker=w)
    job = await jobs.get(again["id"])
    assert job["status"] == "queued" and job["worker_id"] is None
    with pytest.raises(HTTPException):                             # not its job any more
        await workers.progress(again["id"], workers.Progress(), worker=w)


async def test_a_plan_made_by_a_worker_is_finished_by_the_server(monkeypatch):
    monkeypatch.setattr(jobs._wake, "set", lambda: None)
    w = await _worker()
    await jobs.enqueue("plan", ["/m/a.mkv"], options={"where": "worker"})
    got = await workers._claim(w)
    data = {"src_size": 2 * 1024**3, "est_size": 1024**3, "saving": 50.0, "src_mbps": 5.0, "mbps": 2.5, "vmaf": 95.2,
            "crf": 22, "decision": "ok", "note": ""}
    r = await workers.done(got["id"], workers.Done(data=data), worker=w)
    job = await jobs.get(got["id"])
    assert r["ok"] and job["status"] == "done" and job["result"]["crf"] == 22 and "VMAF 95.2" in job["note"]


async def test_a_free_worker_gets_a_head_start_on_whoever_is_free_first(monkeypatch):
    monkeypatch.setattr(jobs._wake, "set", lambda: None)
    w = await _worker()
    await jobs.enqueue("plan", ["/m/a.mkv"], options={"where": "any"})
    with sqlite3.connect(DB_PATH) as conn:                        # the worker asked for work a moment ago
        conn.execute("UPDATE convert_workers SET last_seen = datetime('now', 'localtime') WHERE id = ?", (w["id"],))
    assert await jobs._next() is None                              # the PC is faster: its turn first
    with sqlite3.connect(DB_PATH) as conn:                        # … it did not take it in time
        conn.execute("UPDATE convert_jobs SET created_at = '2000-01-01 00:00:00'")
    assert (await jobs._next())["path"] == "/m/a.mkv"
