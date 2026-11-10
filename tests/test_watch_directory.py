"""watch_directory / poll_watch_events / stop_watch and the notifications they trigger."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
FAST = 0.1


def _wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_watch_reports_created_modified_deleted(client, sandbox):
    w = client.call("watch_directory", directory="resumes", poll_interval_seconds=FAST)["structuredContent"]
    assert w["success"] and w["watch_id"].startswith("w_") and w["resource_uri"] == f"watch://{w['watch_id']}"
    assert len(w["baseline_files"]) == 8 and w["next_seq"] == 1

    new = sandbox / "resumes" / "resume_lena_park.txt"
    shutil.copy(PROJECT / "demo_assets" / "incoming" / "resume_lena_park.txt", new)
    ev = client.call("poll_watch_events", watch_id=w["watch_id"], since_seq=0, timeout_seconds=5)["structuredContent"]
    assert [(e["type"], e["path"]) for e in ev["events"]] == [("created", "resumes/resume_lena_park.txt")]
    assert ev["events"][0]["size_bytes"] == new.stat().st_size
    seq = ev["next_seq"]

    time.sleep(0.05)
    new.write_text(new.read_text() + "\nExtra line\n")
    ev = client.call("poll_watch_events", watch_id=w["watch_id"], since_seq=seq - 1, timeout_seconds=5)["structuredContent"]
    assert [e["type"] for e in ev["events"]] == ["modified"]

    new.unlink()
    ev = client.call("poll_watch_events", watch_id=w["watch_id"], since_seq=ev["next_seq"] - 1, timeout_seconds=5)["structuredContent"]
    assert [e["type"] for e in ev["events"]] == ["deleted"]


def test_extension_filter_and_dotfiles_ignored(client, sandbox):
    w = client.call("watch_directory", directory="resumes", extensions=["pdf"], poll_interval_seconds=FAST)["structuredContent"]
    (sandbox / "resumes" / "notes.txt").write_text("x")
    (sandbox / "resumes" / ".hidden.pdf").write_text("x")
    ev = client.call("poll_watch_events", watch_id=w["watch_id"], timeout_seconds=0.6)["structuredContent"]
    assert ev["events"] == []


def test_long_poll_wakes_up_on_event(client, sandbox):
    w = client.call("watch_directory", directory="resumes", poll_interval_seconds=FAST)["structuredContent"]
    threading.Timer(0.4, lambda: (sandbox / "resumes" / "late.txt").write_text("Late Arrival\nDev")).start()
    t0 = time.monotonic()
    ev = client.call("poll_watch_events", watch_id=w["watch_id"], timeout_seconds=10)["structuredContent"]
    assert ev["count"] == 1 and time.monotonic() - t0 < 5


def test_empty_poll_returns_quickly_with_same_cursor(client):
    w = client.call("watch_directory", directory="resumes", poll_interval_seconds=FAST)["structuredContent"]
    ev = client.call("poll_watch_events", watch_id=w["watch_id"], since_seq=0)["structuredContent"]
    assert ev == {"success": True, "watch_id": w["watch_id"], "events": [], "count": 0, "next_seq": 1}


def test_half_written_file_is_reported_once_settled(client, sandbox):
    w = client.call("watch_directory", directory="resumes", poll_interval_seconds=0.3)["structuredContent"]
    target = sandbox / "resumes" / "big.txt"
    with open(target, "w") as fh:            # keep growing the file across several polls
        for _ in range(4):
            fh.write("x" * 1000)
            fh.flush()
            time.sleep(0.25)
    ev = client.call("poll_watch_events", watch_id=w["watch_id"], timeout_seconds=5)["structuredContent"]
    assert [e["type"] for e in ev["events"]] == ["created"]
    assert ev["events"][0]["size_bytes"] == 4000              # final size, not a partial one


def test_watch_is_idempotent_and_stoppable(client):
    a = client.call("watch_directory", directory="resumes")["structuredContent"]
    b = client.call("watch_directory", directory="resumes")["structuredContent"]
    assert a["watch_id"] == b["watch_id"] and b["already_watching"] is True
    stopped = client.call("stop_watch", watch_id=a["watch_id"])
    assert stopped["isError"] is False
    again = client.call("poll_watch_events", watch_id=a["watch_id"])
    assert again["isError"] and again["structuredContent"]["error"]["type"] == "not_found"
    assert client.call("stop_watch", watch_id=a["watch_id"])["isError"]


def test_watch_errors_and_limits(client_factory):
    c = client_factory(watch_max_watchers=1)
    assert c.call("watch_directory", directory="nope")["isError"]
    assert c.call("watch_directory", directory="../..")["structuredContent"]["error"]["type"] == "outside_sandbox"
    assert c.call("watch_directory", directory="resumes")["isError"] is False
    limit = c.call("watch_directory", directory="output")
    assert limit["isError"] and limit["structuredContent"]["error"]["type"] == "limit_reached"


def test_watch_pushes_notifications(client, sandbox):
    w = client.call("watch_directory", directory="resumes", poll_interval_seconds=FAST)["structuredContent"]
    assert client.request("resources/subscribe", {"uri": w["resource_uri"]})["result"] == {}
    (sandbox / "resumes" / "push.txt").write_text("Push Test\nEngineer")
    assert _wait_for(lambda: any(n["method"] == "notifications/resources/updated" for n in client.notifications))
    methods = [n["method"] for n in client.notifications]
    assert "notifications/resources/list_changed" in methods
    updated = next(n for n in client.notifications if n["method"] == "notifications/resources/updated")
    assert updated["params"] == {"uri": w["resource_uri"]}
    log = [n for n in client.notifications if n["method"] == "notifications/message"
           and n["params"]["data"].get("event") == "watch_events"]
    assert log and log[0]["params"]["data"]["events"][0]["path"] == "resumes/push.txt"
    # the new file is now discoverable as a resource
    uris = [r["uri"] for r in client.request("resources/list")["result"]["resources"]]
    assert "fs:///resumes/push.txt" in uris and w["resource_uri"] in uris


def test_watch_resource_read(client, sandbox):
    import json
    w = client.call("watch_directory", directory="resumes", poll_interval_seconds=FAST)["structuredContent"]
    (sandbox / "resumes" / "r.txt").write_text("R\nX")
    client.call("poll_watch_events", watch_id=w["watch_id"], timeout_seconds=5)
    data = json.loads(client.request("resources/read", {"uri": w["resource_uri"]})["result"]["contents"][0]["text"])
    assert data["event_count"] == 1 and data["recent_events"][0]["path"] == "resumes/r.txt"
