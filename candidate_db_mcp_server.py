"""candidate_db_mcp_server -- a second MCP server: job postings + match history in SQLite.

Bonus "multi-MCP" part of the assignment. Where ``filesystem_mcp_server.py`` implements the
protocol by hand, this one uses the official SDK's ``FastMCP`` -- the agent talks to both
through the same client, which is the point of a standard protocol.

Tools:      list_jobs, get_job, create_job, save_match_results, remove_matches, get_shortlist,
            get_candidate_history
Resources:  db://jobs, db://jobs/{job_id}, db://jobs/{job_id}/shortlist
Prompt:     compare_candidates

Run:  python candidate_db_mcp_server.py [--db data/candidates.db]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "candidates.db"

SEED_JOBS: list[dict[str, Any]] = [
    {
        "job_id": "senior-python-backend",
        "title": "Senior Python Backend Engineer",
        "team": "Payments Platform",
        "location": "Remote (US)",
        "min_years": 5,
        "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"],
        "nice_to_have": ["Kubernetes", "Redis", "CI/CD", "Terraform", "Microservices"],
        "description": "Own high-throughput Python services (FastAPI, PostgreSQL) running on AWS. "
                       "You will design APIs, containerise services with Docker and mentor engineers.",
    },
    {
        "job_id": "ml-engineer-nlp",
        "title": "Machine Learning Engineer, NLP",
        "team": "Search Relevance",
        "location": "Boston, MA / Hybrid",
        "min_years": 3,
        "required_skills": ["Python", "PyTorch", "NLP", "Transformers"],
        "nice_to_have": ["MLOps", "Docker", "AWS", "FastAPI", "scikit-learn"],
        "description": "Train and ship transformer models for semantic search and classification, "
                       "from research prototype to production APIs.",
    },
    {
        "job_id": "platform-sre",
        "title": "Site Reliability Engineer",
        "team": "Core Infrastructure",
        "location": "Denver, CO",
        "min_years": 4,
        "required_skills": ["Kubernetes", "Terraform", "AWS", "Python", "CI/CD"],
        "nice_to_have": ["Go", "Prometheus", "Grafana", "Linux", "Docker"],
        "description": "Run our Kubernetes platform on AWS, codify infrastructure with Terraform and "
                       "build automation in Python or Go.",
    },
    {
        "job_id": "frontend-engineer",
        "title": "Frontend Engineer",
        "team": "Design Systems",
        "location": "Vancouver, BC",
        "min_years": 3,
        "required_skills": ["React", "TypeScript", "CSS", "Testing"],
        "nice_to_have": ["Next.js", "Accessibility", "GraphQL", "CI/CD"],
        "description": "Build accessible, fast React + TypeScript interfaces and evolve our design system.",
    },
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    team TEXT, location TEXT,
    min_years INTEGER NOT NULL DEFAULT 0,
    required_skills TEXT NOT NULL,
    nice_to_have TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    filepath TEXT NOT NULL,
    candidate_name TEXT NOT NULL,
    score REAL NOT NULL,
    tier TEXT,
    years_experience INTEGER,
    required_matched TEXT, required_missing TEXT, nice_matched TEXT,
    assessment TEXT,
    run_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, filepath)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    candidates INTEGER NOT NULL,
    top_candidate TEXT,
    recorded_at TEXT NOT NULL
);
"""

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CandidateDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with _lock, self.conn:
            self.conn.executescript(SCHEMA)
            if self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0:
                for job in SEED_JOBS:
                    self._insert_job(job)

    def _insert_job(self, job: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
            (job["job_id"], job["title"], job.get("team"), job.get("location"), int(job.get("min_years", 0)),
             json.dumps(job["required_skills"]), json.dumps(job.get("nice_to_have", [])),
             job.get("description", ""), _now()))

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["required_skills"] = json.loads(d["required_skills"])
        d["nice_to_have"] = json.loads(d["nice_to_have"])
        return d

    def jobs(self) -> list[dict[str, Any]]:
        return [self._job_row(r) for r in self.conn.execute("SELECT * FROM jobs ORDER BY title")]

    def job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_row(row) if row else None

    def shortlist(self, job_id: str, limit: int, min_score: float) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM matches WHERE job_id = ? AND score >= ? ORDER BY score DESC, candidate_name LIMIT ?",
            (job_id, min_score, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("required_matched", "required_missing", "nice_matched"):
                d[k] = json.loads(d[k] or "[]")
            out.append(d)
        return out


def build_server(db_path: Path) -> FastMCP:
    db = CandidateDB(db_path)
    mcp = FastMCP(
        "candidate-db-mcp",
        instructions="Job postings and resume-match history. Get a job's requirements with get_job, store "
                     "ranked results with save_match_results, read the best candidates with get_shortlist.",
    )

    def _require_job(job_id: str) -> dict[str, Any]:
        job = db.job(job_id)
        if job is None:
            known = ", ".join(j["job_id"] for j in db.jobs())
            raise ValueError(f"Unknown job_id {job_id!r}. Known jobs: {known}")
        return job

    @mcp.tool()
    def list_jobs() -> dict[str, Any]:
        """List every open job posting (id, title, team, required skills)."""
        jobs = db.jobs()
        return {"count": len(jobs), "jobs": [{k: j[k] for k in ("job_id", "title", "team", "location",
                                                                "min_years", "required_skills")} for j in jobs]}

    @mcp.tool()
    def get_job(job_id: str) -> dict[str, Any]:
        """Full job posting: description, required and nice-to-have skills, minimum years."""
        return _require_job(job_id)

    @mcp.tool()
    def create_job(job_id: str, title: str, required_skills: list[str], nice_to_have: list[str] | None = None,
                   min_years: int = 0, description: str = "", team: str = "", location: str = "") -> dict[str, Any]:
        """Create a job posting. job_id must be a lowercase slug (letters, digits, dashes)."""
        if not _SLUG.match(job_id):
            raise ValueError("job_id must be a lowercase slug like 'data-engineer'")
        if not required_skills:
            raise ValueError("required_skills must not be empty")
        if db.job(job_id):
            raise ValueError(f"job_id {job_id!r} already exists")
        job = {"job_id": job_id, "title": title, "team": team, "location": location, "min_years": min_years,
               "required_skills": required_skills, "nice_to_have": nice_to_have or [], "description": description}
        with _lock, db.conn:
            db._insert_job(job)
        return {"created": True, "job": db.job(job_id)}

    @mcp.tool()
    def save_match_results(job_id: str, run_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Upsert scored candidates for a job. Each result needs filepath, name and score; optional tier,
        years_experience, required_matched, required_missing, nice_matched, assessment."""
        _require_job(job_id)
        now = _now()
        saved = 0
        with _lock, db.conn:
            for r in results:
                if not {"filepath", "name", "score"} <= set(r):
                    raise ValueError("each result needs filepath, name and score")
                db.conn.execute(
                    "INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(job_id, filepath) DO UPDATE SET candidate_name=excluded.candidate_name, "
                    "score=excluded.score, tier=excluded.tier, years_experience=excluded.years_experience, "
                    "required_matched=excluded.required_matched, required_missing=excluded.required_missing, "
                    "nice_matched=excluded.nice_matched, assessment=COALESCE(excluded.assessment, matches.assessment), "
                    "run_id=excluded.run_id, updated_at=excluded.updated_at",
                    (job_id, r["filepath"], r["name"], float(r["score"]), r.get("tier"), r.get("years_experience"),
                     json.dumps(r.get("required_matched", [])), json.dumps(r.get("required_missing", [])),
                     json.dumps(r.get("nice_matched", [])), r.get("assessment"), run_id, now))
                saved += 1
            top = max(results, key=lambda r: r["score"])["name"] if results else None
            db.conn.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)", (run_id, job_id, saved, top, now))
        total = db.conn.execute("SELECT COUNT(*) FROM matches WHERE job_id = ?", (job_id,)).fetchone()[0]
        return {"saved": saved, "job_id": job_id, "run_id": run_id, "candidates_on_record": total}

    @mcp.tool()
    def remove_matches(job_id: str, filepaths: list[str]) -> dict[str, Any]:
        """Delete stored scores for resumes that no longer exist (e.g. removed from the folder)."""
        _require_job(job_id)
        with _lock, db.conn:
            removed = sum(db.conn.execute("DELETE FROM matches WHERE job_id = ? AND filepath = ?", (job_id, fp)).rowcount
                          for fp in filepaths)
        return {"removed": removed, "job_id": job_id}

    @mcp.tool()
    def get_shortlist(job_id: str, limit: int = 5, min_score: float = 0) -> dict[str, Any]:
        """Best stored candidates for a job, highest score first."""
        _require_job(job_id)
        rows = db.shortlist(job_id, max(1, min(limit, 100)), min_score)
        return {"job_id": job_id, "count": len(rows), "shortlist": rows}

    @mcp.tool()
    def get_candidate_history(filepath: str) -> dict[str, Any]:
        """Every job a resume has been scored against, with its score there."""
        rows = db.conn.execute(
            "SELECT m.job_id, j.title, m.score, m.tier, m.updated_at FROM matches m JOIN jobs j USING(job_id) "
            "WHERE m.filepath = ? ORDER BY m.score DESC", (filepath,)).fetchall()
        return {"filepath": filepath, "count": len(rows), "history": [dict(r) for r in rows]}

    @mcp.resource("db://jobs", mime_type="application/json")
    def jobs_resource() -> str:
        """All job postings."""
        return json.dumps(db.jobs(), indent=1)

    @mcp.resource("db://jobs/{job_id}", mime_type="application/json")
    def job_resource(job_id: str) -> str:
        """One job posting."""
        return json.dumps(_require_job(job_id), indent=1)

    @mcp.resource("db://jobs/{job_id}/shortlist", mime_type="application/json")
    def shortlist_resource(job_id: str) -> str:
        """Top 10 stored candidates for a job."""
        _require_job(job_id)
        return json.dumps(db.shortlist(job_id, 10, 0), indent=1)

    @mcp.prompt()
    def compare_candidates(job_id: str) -> str:
        """Ask the model to compare the shortlist for a job."""
        return (f"Call get_job('{job_id}') and get_shortlist('{job_id}'). Compare the top three candidates "
                "against the required skills and recommend who to interview first, with reasons.")

    return mcp


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Candidate database MCP server (stdio)")
    p.add_argument("--db", default=os.environ.get("CANDIDATE_DB", str(DEFAULT_DB)))
    args = p.parse_args(argv)
    build_server(Path(args.db)).run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
