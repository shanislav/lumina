"""One file in parts (convert where = "together"): the cuts, who takes which part, a part run twice, the leader."""

import json
import os
import sqlite3

import pytest
from fastapi import HTTPException

from app.core import registry
from app.db import DB_PATH, init_db
from app.modules.convert import jobs, shrink, workers


def test_cuts_sit_on_keyframes_about_every_two_minutes(monkeypatch):
    fps = 24
    times = [i / fps for i in range(600 * fps)]                    # 10 min
    keys = [t for t in range(0, 600, 5) if not 230 <= t <= 270]    # a long shot around 4 min: no keyframe there
    monkeypatch.setattr(shrink, "frame_times", lambda path: (times, [float(k) for k in keys]))
    # a new shot begins at 2:10 (the keyframe of 2:00 is only the encoder's regular one)
    monkeypatch.setattr(shrink, "scene_scores", lambda path, a, b: {130.0: 0.8, 120.0: 0.05})
    parts = shrink.split("/m/film.mkv")
    starts = [p["start"] for p in parts]
    assert starts[0] == 0 and all(s in keys for s in starts)
    assert sum(p["frames"] for p in parts) == len(times)
    assert starts[1] == 130 and 225 <= starts[2] <= 275 and all(b - a >= 100 for a, b in zip(starts, starts[1:]))
    assert shrink.split("/m/film.ts") == []                         # a transport stream is not cut


async def _setup(monkeypatch, x265="4.3+45"):
    monkeypatch.setattr(jobs._wake, "set", lambda: None)
    monkeypatch.setattr(shrink, "x265_version", lambda: "4.2+1")
    await init_db(registry.discover())
    added = await workers.add_worker(workers.NewWorker(name="Notebook"))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE convert_workers SET info = ? WHERE id = ?",
                     (json.dumps({"encoders": ["libx265"], "x265": x265}), added["id"]))
        conn.execute("INSERT INTO convert_jobs (id, kind, path, status, options) VALUES (1, 'shrink', '/m/f.mkv', "
                     "'running', ?)", (json.dumps({"where": "together", "crf": 22}),))
        for i in range(2):
            conn.execute("INSERT INTO convert_jobs (kind, path, options) VALUES ('part', '/m/f.mkv', ?)",
                         (json.dumps({"parent": 1, "idx": i, "count": 2, "start": i * 120.0, "frames": 100}),))
        conn.commit()
        return dict(conn.execute("SELECT * FROM convert_workers WHERE id = ?", (added["id"],)).fetchone())


async def test_parts_go_to_the_server_and_a_worker_and_a_stuck_one_runs_twice(monkeypatch):
    w = await _setup(monkeypatch)
    mine = await workers._claim(w)
    assert mine["kind"] == "part" and mine["options"]["idx"] == 0
    assert mine["work"] == "/m/.lumina-convert/1" and mine["out_name"] == f"part-0000-{mine['id']}.mkv"
    server = await jobs.claim_part(None, parent=1)
    assert server["options"]["idx"] == 1
    assert await jobs.part_done(server, "/nowhere/part-0001.mkv", 50.0)
    # nothing left to start; the worker paused (its user plays a game) → the server takes its part too
    await workers.progress(mine["id"], workers.Progress(phase="encode", value=40, paused="hra"), worker=w)
    again = await jobs.claim_part(None, parent=1)
    assert again["options"]["idx"] == 0 and again["id"] != mine["id"]
    assert await jobs.claim_part(None, parent=1) is None            # never a third time
    assert await jobs.part_done(again, "/nowhere/part-0000.mkv", 60.0)
    with pytest.raises(HTTPException):                              # the worker is told to stop
        await workers.progress(mine["id"], workers.Progress(), worker=w)
    parts = await jobs.parts_of(1)
    assert [(p["options"]["idx"], p["status"]) for p in parts] == [(0, "done"), (1, "done")]


async def test_a_silent_workers_part_comes_back_once_and_a_failing_part_fails_the_job(monkeypatch):
    w = await _setup(monkeypatch)
    mine = await workers._claim(w)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE convert_jobs SET lease_until = '2000-01-01 00:00:00' WHERE id = ?", (mine["id"],))
    await jobs.release_expired()
    assert [p["status"] for p in await jobs.parts_of(1)] == ["queued", "queued"]
    for _ in range(jobs.PART_FAILS):
        part = await jobs.claim_part(None, parent=1)
        await jobs.part_back(part, "ffmpeg: chyba")
    assert any(p["status"] == "error" for p in await jobs.parts_of(1))


async def test_another_x265_gets_no_parts(monkeypatch):
    w = await _setup(monkeypatch, x265="3.5")
    assert "jiná verze x265" in workers.parts_ok(json.loads(w["info"]))
    assert await workers._claim(w) is None


async def test_the_leader_cuts_encodes_joins_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs._wake, "set", lambda: None)
    await init_db(registry.discover())
    src = tmp_path / "Film.mkv"
    src.write_bytes(b"x" * 1000)
    info = {"dv": False, "duration": 360.0, "fps": 24.0, "audio": []}
    monkeypatch.setattr(shrink, "probe", lambda p: info)
    monkeypatch.setattr(shrink, "split", lambda p: [{"idx": i, "start": i * 120.0, "frames": 2880} for i in range(3)])
    encoded = []

    def fake_part(path, start, frames, info, opts, crf, out, progress=None, cancel=None):
        encoded.append(start)
        with open(out, "wb") as f:
            f.write(b"p")
        return 1.0
    monkeypatch.setattr(shrink, "encode_part", fake_part)
    joined = {}

    def fake_assemble(path, files, counts, info, opts, work, seams, progress=None):
        joined.update(files=[os.path.basename(f) for f in files], frames=sum(counts), seams=seams)
        out = os.path.join(work, "Film.mkv")
        with open(out, "wb") as f:
            f.write(b"new")
        return out, {"vmaf_check": [96.0], "size": 3}
    monkeypatch.setattr(shrink, "assemble", fake_assemble)

    async def fake_done(job, out, check):
        return {"path": str(src), "note": "ok", "data": check}
    monkeypatch.setattr(jobs, "shrink_done", fake_done)
    await jobs.enqueue("shrink", [str(src)], options={"where": "together", "crf": 22})
    job = await jobs._next()
    assert job and jobs._together(job)
    result = await jobs.run(job)
    assert encoded == [0.0, 120.0, 240.0] and joined["frames"] == 3 * 2880 and joined["seams"] == [120.0, 240.0]
    assert [f.split("-")[1] for f in joined["files"]] == ["0000", "0001", "0002"]
    assert "3 částí (server)" in result["note"]
    assert await jobs.parts_of(job["id"]) == [] and not os.path.exists(jobs.work_dir(str(src), job["id"]))
    assert all(j["kind"] != "part" for j in await jobs.recent())
