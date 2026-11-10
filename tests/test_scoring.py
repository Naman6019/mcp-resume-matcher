"""resume_scoring: skill detection, weak mentions, experience and ranking on the sample corpus."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import fs_tools
from resume_scoring import JobSpec, estimate_years, extract_skills, find_skill, rank, render_report, score_resume

PROJECT = Path(__file__).resolve().parent.parent
BACKEND = JobSpec("senior-python-backend", "Senior Python Backend Engineer",
                  ["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"],
                  ["Kubernetes", "Redis", "CI/CD", "Terraform", "Microservices"], 5)


def _corpus(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("FS_ROOT", str(PROJECT))
    return {f["path"]: fs_tools.read_file(f["path"])["content"] for f in fs_tools.list_files("resumes")}


def test_weak_mentions_are_partial():
    assert find_skill("Languages: TypeScript; basic Python", "Python").strength == "partial"
    assert find_skill("completed an introductory Python course", "Python").strength == "partial"
    assert find_skill("Tools: Git, Docker (basic), Linux", "Docker").strength == "partial"
    assert find_skill("Tools: Git, Docker (basic), Linux, PostgreSQL", "PostgreSQL").strength == "strong"
    assert find_skill("basic HTML. Built Python services", "Python").strength == "strong"
    assert find_skill("Some JavaScript\n8 years of Python", "Python").strength == "strong"


def test_skill_patterns_avoid_false_positives():
    assert find_skill("JavaScript and TypeScript", "Java") is None
    assert find_skill("we go to market", "Go") is None
    assert find_skill("Uses Python and Go for tooling", "Go") is not None
    assert find_skill("AWS EKS clusters", "Kubernetes") is not None


def test_estimate_years():
    assert estimate_years("Engineer with 8 years of experience") == 8
    assert estimate_years("Acme (2018 - 2021)\nBeta (2021 - Present)", today=date(2026, 1, 1)) == 8
    assert estimate_years("no dates here") is None


def test_extract_skills_from_free_text():
    assert extract_skills("We need a React + TypeScript dev who writes Jest tests") == ["React", "TypeScript", "Testing"]


def test_backend_ranking_on_sample_corpus(monkeypatch):
    ranked = rank([score_resume(t, p, BACKEND) for p, t in _corpus(monkeypatch).items()])
    names = [c.name for c in ranked]
    assert names[0] == "John Doe" and ranked[0].tier == "Strong match" and ranked[0].required_missing == []
    assert names[-2:] == ["Alex Chen", "Emily Watson"]           # only weak Python mentions
    alex = next(c for c in ranked if c.name == "Alex Chen")
    assert alex.required_partial == ["Python"]
    assert all(0 <= c.score <= 100 for c in ranked)
    assert ranked == sorted(ranked, key=lambda c: -c.score)


def test_other_roles_pick_the_obvious_candidate(monkeypatch):
    corpus = _corpus(monkeypatch)
    roles = {
        "Sara Lindqvist": JobSpec("ml", "ML", ["Python", "PyTorch", "NLP", "Transformers"], ["MLOps"], 3),
        "Maria Garcia": JobSpec("sre", "SRE", ["Kubernetes", "Terraform", "AWS", "Python", "CI/CD"], ["Go"], 4),
        "Alex Chen": JobSpec("fe", "FE", ["React", "TypeScript", "CSS", "Testing"], ["Accessibility"], 3),
    }
    for expected, job in roles.items():
        assert rank([score_resume(t, p, job) for p, t in corpus.items()])[0].name == expected


def test_report_markdown(monkeypatch):
    corpus = _corpus(monkeypatch)
    ranked = rank([score_resume(t, p, BACKEND) for p, t in corpus.items()])
    md = render_report(BACKEND, ranked, "2026-11-11 10:00", "run_x", {"resumes": "filesystem-mcp"})
    assert md.startswith("# Match report: Senior Python Backend Engineer")
    assert "| 1 | John Doe |" in md and "run_x" in md and "filesystem-mcp" in md
