"""resume_scoring -- deterministic, explainable resume <-> job matching.

Pure functions with no I/O: the agent fetches text through MCP and hands it here.
Keeping scoring deterministic means rankings are reproducible and testable; the
optional LLM step in ``matching_agent`` only *explains* scores, it never changes them.

Score (0-100) = 60 x required-skill coverage
              + 20 x nice-to-have coverage
              + 15 x experience fit  (years / min_years, capped at 1)
              +  5 x depth           (how often required skills appear, capped)

A skill only mentioned with a weak qualifier ("basic Python", "introductory Python
course", "some JavaScript") counts as *partial* (0.4) rather than a full match.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

# canonical skill -> regex patterns (case-insensitive unless the pattern sets (?-i))
SKILL_PATTERNS: dict[str, list[str]] = {
    "Python": [r"\bpython\b"],
    "FastAPI": [r"\bfastapi\b"],
    "Django": [r"\bdjango\b"],
    "Flask": [r"\bflask\b"],
    "PostgreSQL": [r"\bpostgres(?:ql)?\b"],
    "SQL": [r"\bsql\b"],
    "Redis": [r"\bredis\b"],
    "Celery": [r"\bcelery\b"],
    "Docker": [r"\bdocker\b"],
    "Kubernetes": [r"\bkubernetes\b", r"\bk8s\b", r"\beks\b", r"\bhelm\b"],
    "AWS": [r"\baws\b", r"\bamazon web services\b", r"\bsagemaker\b"],
    "GCP": [r"\bgcp\b", r"\bgoogle cloud\b"],
    "Terraform": [r"\bterraform\b"],
    "CI/CD": [r"\bci/cd\b", r"\bgithub actions\b", r"\bgitlab ci\b", r"\bjenkins\b", r"\bargocd\b", r"\blighthouse ci\b"],
    "Microservices": [r"\bmicroservices?\b"],
    "REST APIs": [r"\brest(?:ful)?\b(?:\s+\w+)?\s+apis?\b", r"\brest\b"],
    "Kafka": [r"\bkafka\b"],
    "Linux": [r"\blinux\b"],
    "Go": [r"(?-i:\bGo\b)", r"\bgolang\b"],
    "Java": [r"\bjava\b(?!script)"],
    "Kotlin": [r"\bkotlin\b"],
    "Prometheus": [r"\bprometheus\b"],
    "Grafana": [r"\bgrafana\b"],
    "PyTorch": [r"\bpytorch\b"],
    "TensorFlow": [r"\btensorflow\b"],
    "Transformers": [r"\btransformers?\b", r"\bhugging face\b"],
    "NLP": [r"\bnlp\b", r"\bnatural language processing\b", r"\bnamed-entity\b", r"\bnltk\b", r"\bspacy\b"],
    "scikit-learn": [r"\bscikit-learn\b", r"\bsklearn\b"],
    "pandas": [r"\bpandas\b"],
    "MLOps": [r"\bmlops\b", r"\bmlflow\b", r"\bsagemaker\b"],
    "Airflow": [r"\bairflow\b"],
    "React": [r"\breact\b"],
    "TypeScript": [r"\btypescript\b"],
    "JavaScript": [r"\bjavascript\b"],
    "Next.js": [r"\bnext\.js\b"],
    "CSS": [r"\bcss\b", r"\bscss\b", r"\btailwind\b"],
    "Testing": [r"\bpytest\b", r"\bjest\b", r"\bcypress\b", r"\bjunit\b", r"\bunit tests?\b"],
    "Accessibility": [r"\baccessib(?:le|ility)\b", r"\bwcag\b", r"\ba11y\b"],
    "GraphQL": [r"\bgraphql\b"],
}

WEAK_QUALIFIER = re.compile(r"\b(basic|some|introductory|beginner|familiar(?:ity)? with|exposure to|learning)\b[^.\n;|,()]{0,25}$", re.I)
WEAK_SUFFIX = re.compile(r"^\s*\((?:basic|beginner|learning|some)\)", re.I)
PARTIAL_CREDIT = 0.4
YEARS_RE = re.compile(r"(\d{1,2})\+?\s*(?:years|yrs)\b", re.I)
RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*[-\u2013]\s*((?:19|20)\d{2}|present|current|now)\b", re.I)


@dataclass
class SkillHit:
    skill: str
    strength: str           # "strong" | "partial"
    mentions: int
    evidence: str           # the line where it first appears


@dataclass
class JobSpec:
    job_id: str
    title: str
    required_skills: list[str]
    nice_to_have: list[str] = field(default_factory=list)
    min_years: int = 0
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JobSpec":
        return cls(job_id=d.get("job_id") or d.get("id") or "adhoc", title=d.get("title", "Untitled role"),
                   required_skills=list(d.get("required_skills") or []), nice_to_have=list(d.get("nice_to_have") or []),
                   min_years=int(d.get("min_years") or 0), description=d.get("description", ""))


@dataclass
class CandidateScore:
    filepath: str
    name: str
    headline: str
    score: float
    tier: str
    years_experience: int | None
    required_matched: list[str]
    required_partial: list[str]
    required_missing: list[str]
    nice_matched: list[str]
    evidence: dict[str, str]
    breakdown: dict[str, float]
    assessment: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def find_skill(text: str, skill: str) -> SkillHit | None:
    patterns = SKILL_PATTERNS.get(skill) or [rf"\b{re.escape(skill)}\b"]
    hits: list[re.Match[str]] = []
    for pat in patterns:
        hits.extend(re.finditer(pat, text, re.I))
    if not hits:
        return None
    hits.sort(key=lambda m: m.start())
    strong = []
    for m in hits:
        line_start = text.rfind("\n", 0, m.start()) + 1
        prefix = text[line_start:m.start()]
        if not WEAK_QUALIFIER.search(prefix) and not WEAK_SUFFIX.match(text[m.end():m.end() + 20]):
            strong.append(m)
    first = (strong or hits)[0]
    line_start = text.rfind("\n", 0, first.start()) + 1
    line_end = text.find("\n", first.end())
    evidence = text[line_start: line_end if line_end != -1 else len(text)].strip(" *\u2022\t")
    return SkillHit(skill, "strong" if strong else "partial", len(hits), evidence[:160])


def extract_skills(text: str) -> list[str]:
    """Every taxonomy skill that appears in ``text`` (used to parse ad-hoc job descriptions)."""
    return [s for s in SKILL_PATTERNS if find_skill(text, s)]


def estimate_years(text: str, today: date | None = None) -> int | None:
    """Explicit 'N years' claims win; otherwise the span of dated roles."""
    explicit = [int(n) for n in YEARS_RE.findall(text) if 0 < int(n) < 50]
    if explicit:
        return max(explicit)
    year_now = (today or date.today()).year
    starts, ends = [], []
    for a, b in RANGE_RE.findall(text):
        starts.append(int(a))
        ends.append(year_now if not b[0].isdigit() else int(b))
    if starts:
        return max(0, max(ends) - min(starts))
    return None


def parse_identity(text: str, filepath: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    name = lines[0].title() if lines else filepath
    headline = lines[1] if len(lines) > 1 else ""
    return name, headline


def score_resume(text: str, filepath: str, job: JobSpec, today: date | None = None) -> CandidateScore:
    name, headline = parse_identity(text, filepath)
    req_hits = {s: find_skill(text, s) for s in job.required_skills}
    nice_hits = {s: find_skill(text, s) for s in job.nice_to_have}

    matched = [s for s, h in req_hits.items() if h and h.strength == "strong"]
    partial = [s for s, h in req_hits.items() if h and h.strength == "partial"]
    missing = [s for s, h in req_hits.items() if not h]
    nice = [s for s, h in nice_hits.items() if h]

    req_cov = ((len(matched) + PARTIAL_CREDIT * len(partial)) / len(job.required_skills)) if job.required_skills else 1.0
    nice_cov = (len(nice) / len(job.nice_to_have)) if job.nice_to_have else 0.0
    years = estimate_years(text, today)
    exp_fit = 1.0 if job.min_years <= 0 else min(1.0, (years or 0) / job.min_years)
    mentions = sum(h.mentions for h in req_hits.values() if h)
    depth = min(1.0, mentions / max(1, 3 * len(job.required_skills)))

    breakdown = {
        "required": round(60 * req_cov, 1),
        "nice_to_have": round(20 * nice_cov, 1),
        "experience": round(15 * exp_fit, 1),
        "depth": round(5 * depth, 1),
    }
    total = round(sum(breakdown.values()), 1)
    tier = "Strong match" if total >= 75 else "Potential match" if total >= 55 else "Not a fit"
    evidence = {h.skill: h.evidence for h in list(req_hits.values()) + list(nice_hits.values()) if h}
    return CandidateScore(filepath, name, headline, total, tier, years, matched, partial, missing, nice,
                          evidence, breakdown)


def rank(candidates: list[CandidateScore]) -> list[CandidateScore]:
    return sorted(candidates, key=lambda c: (-c.score, c.name))


def render_report(job: JobSpec, ranked: list[CandidateScore], generated_at: str, run_id: str,
                  sources: dict[str, str] | None = None) -> str:
    out = [f"# Match report: {job.title}", "",
           f"*Job id `{job.job_id}` - run `{run_id}` - generated {generated_at}*", "",
           f"**Required:** {', '.join(job.required_skills) or '-'}  ",
           f"**Nice to have:** {', '.join(job.nice_to_have) or '-'}  ",
           f"**Minimum experience:** {job.min_years} years", "",
           "| Rank | Candidate | Score | Tier | Years | Missing required |",
           "|---:|---|---:|---|---:|---|"]
    for i, c in enumerate(ranked, 1):
        out.append(f"| {i} | {c.name} | {c.score} | {c.tier} | {c.years_experience if c.years_experience is not None else '?'} "
                   f"| {', '.join(c.required_missing + [p + ' (weak)' for p in c.required_partial]) or '-'} |")
    out.append("")
    for i, c in enumerate(ranked, 1):
        out += [f"## {i}. {c.name} - {c.score}/100", f"*{c.headline}* - `{c.filepath}`", ""]
        if c.assessment:
            out += [f"> {c.assessment}", ""]
        out.append(f"- Matched: {', '.join(c.required_matched) or '-'}")
        if c.required_partial:
            out.append(f"- Only weak mentions: {', '.join(c.required_partial)}")
        out.append(f"- Missing: {', '.join(c.required_missing) or '-'}")
        out.append(f"- Nice to have: {', '.join(c.nice_matched) or '-'}")
        out.append(f"- Score breakdown: " + ", ".join(f"{k} {v}" for k, v in c.breakdown.items()))
        for skill in (c.required_matched + c.required_partial)[:4]:
            out.append(f"  - *{skill}*: \"{c.evidence.get(skill, '')}\"")
        out.append("")
    if sources:
        out += ["---", "Data sources: " + ", ".join(f"{k} via `{v}`" for k, v in sources.items())]
    return "\n".join(out) + "\n"
