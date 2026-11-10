"""Generate the dummy resume corpus in ``resumes/`` (3 PDF, 3 DOCX, 2 TXT).

All candidates are fictional; e-mail addresses use ``example.com`` and phone numbers use the
reserved 555-01xx range.  The corpus is deliberately varied so the demo queries are
interesting:

* strong Python profiles   -> john_doe, priya_sharma, sara_lindqvist, rahul_mehta
* Python as a side skill   -> alex_chen ("basic Python scripting"), maria_garcia (automation)
* no Python experience     -> david_okafor (Java/Kotlin), emily_watson (only an intro course)

Run ``python generate_resumes.py`` to (re)build the files.  The output is deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).resolve().parent / "resumes"

Resume = dict[str, Any]

RESUMES: list[Resume] = [
    {
        "slug": "john_doe", "fmt": "pdf",
        "name": "John Doe", "title": "Senior Backend Engineer",
        "contact": "john.doe@example.com | +1 (555) 010-0142 | Austin, TX | github.com/example-jdoe",
        "summary": (
            "Backend engineer with 8 years of professional Python experience building high-throughput "
            "REST and event-driven services. Led a team of five migrating a Django monolith to FastAPI "
            "microservices on AWS, cutting p95 latency by 60%."
        ),
        "experience": [
            {"role": "Senior Backend Engineer", "company": "Northwind Logistics", "dates": "2021 - Present",
             "bullets": [
                 "Designed Python (FastAPI, SQLAlchemy) microservices handling 4,000 requests/second.",
                 "Introduced Celery + Redis task queues, reducing batch-processing time from 6 hours to 40 minutes.",
                 "Mentored 5 engineers; established code-review and pytest coverage standards (92% coverage).",
             ]},
            {"role": "Backend Engineer", "company": "Brightpath Software", "dates": "2018 - 2021",
             "bullets": [
                 "Built Django REST Framework APIs for a SaaS billing platform serving 12,000 customers.",
                 "Wrote Python data-migration scripts that moved 80M rows between PostgreSQL clusters with zero downtime.",
             ]},
            {"role": "Software Developer", "company": "Cobalt Systems", "dates": "2016 - 2018",
             "bullets": ["Maintained internal Python tooling and Flask services; automated deployments with Fabric."]},
        ],
        "education": ["B.S. Computer Science, University of Texas at Austin, 2016"],
        "skills": {
            "Languages": "Python, SQL, Bash, some JavaScript",
            "Frameworks": "FastAPI, Django, Flask, Celery, SQLAlchemy",
            "Cloud & DevOps": "AWS (EC2, RDS, SQS, Lambda), Docker, Terraform, GitHub Actions",
            "Databases": "PostgreSQL, Redis, DynamoDB",
        },
        "certifications": ["AWS Certified Solutions Architect - Associate (2022)"],
    },
    {
        "slug": "priya_sharma", "fmt": "docx",
        "name": "Priya Sharma", "title": "Data Scientist",
        "contact": "priya.sharma@example.com | +1 (555) 010-0177 | Seattle, WA | linkedin.com/in/example-priya",
        "summary": (
            "Data scientist with 6 years of experience applying Python, statistics and machine learning "
            "to forecasting and customer analytics. Comfortable taking models from notebook to production."
        ),
        "experience": [
            {"role": "Senior Data Scientist", "company": "Cascade Retail Group", "dates": "2022 - Present",
             "bullets": [
                 "Built demand-forecasting models in Python (pandas, scikit-learn, XGBoost) that improved forecast accuracy by 18%.",
                 "Productionised a PyTorch churn model served via FastAPI; reduced monthly churn by 2.3 points.",
                 "Created Airflow DAGs and dbt models feeding a company-wide experimentation platform.",
             ]},
            {"role": "Data Analyst", "company": "Lumen Health Analytics", "dates": "2019 - 2022",
             "bullets": [
                 "Automated weekly reporting with Python and SQL, saving 15 analyst-hours per week.",
                 "Designed A/B tests for patient-engagement features; presented results to executive leadership.",
             ]},
        ],
        "education": [
            "M.S. Statistics, University of Washington, 2019",
            "B.Tech Computer Engineering, Pune University, 2017",
        ],
        "skills": {
            "Languages": "Python, R, SQL",
            "ML / Data": "pandas, NumPy, scikit-learn, XGBoost, PyTorch, statsmodels",
            "Tools": "Airflow, dbt, Snowflake, Tableau, Git, Docker",
            "Practices": "A/B testing, causal inference, time-series forecasting",
        },
        "certifications": ["Google Professional Data Engineer (2023)"],
    },
    {
        "slug": "alex_chen", "fmt": "txt",
        "name": "Alex Chen", "title": "Frontend Engineer",
        "contact": "alex.chen@example.com | +1 (555) 010-0113 | Vancouver, BC | alexchen.example.dev",
        "summary": (
            "Frontend engineer with 5 years of experience building accessible, high-performance "
            "React and TypeScript applications. Strong eye for design systems and web performance."
        ),
        "experience": [
            {"role": "Frontend Engineer", "company": "Pixelforge Studios", "dates": "2021 - Present",
             "bullets": [
                 "Led the rebuild of a design system (React, TypeScript, Storybook) adopted by 9 product teams.",
                 "Improved Largest Contentful Paint from 4.1s to 1.6s through code-splitting and image optimisation.",
                 "Wrote basic Python scripts to automate asset pipelines and generate localisation files.",
             ]},
            {"role": "Junior Web Developer", "company": "Harbor Digital", "dates": "2019 - 2021",
             "bullets": [
                 "Developed responsive marketing sites with Vue.js and SCSS for 20+ clients.",
                 "Introduced automated accessibility testing (axe, Lighthouse CI) into the release pipeline.",
             ]},
        ],
        "education": ["B.Sc. Interactive Arts and Technology, Simon Fraser University, 2019"],
        "skills": {
            "Languages": "TypeScript, JavaScript, HTML, CSS/SCSS; basic Python",
            "Frameworks": "React, Next.js, Vue.js, Redux, Tailwind CSS",
            "Tooling": "Vite, Webpack, Jest, Cypress, Storybook, Figma, GitHub Actions",
        },
        "certifications": ["Certified Professional in Web Accessibility (WAS), IAAP (2023)"],
    },
    {
        "slug": "maria_garcia", "fmt": "pdf",
        "name": "Maria Garcia", "title": "Site Reliability Engineer",
        "contact": "maria.garcia@example.com | +1 (555) 010-0158 | Denver, CO | github.com/example-mgarcia",
        "summary": (
            "SRE with 7 years of experience running Kubernetes platforms and automating infrastructure. "
            "Uses Python and Go for tooling, and has driven reliability work that held 99.99% availability."
        ),
        "experience": [
            {"role": "Senior Site Reliability Engineer", "company": "Stratus Cloud Services", "dates": "2020 - Present",
             "bullets": [
                 "Operate 40+ Kubernetes clusters on AWS EKS; authored Terraform modules reused by 12 teams.",
                 "Wrote Python automation (boto3, Click) for capacity reporting and on-call runbooks, cutting toil by 35%.",
                 "Defined SLOs and error budgets; reduced incident MTTR from 52 to 19 minutes with Prometheus and Grafana.",
             ]},
            {"role": "DevOps Engineer", "company": "Redwood Fintech", "dates": "2017 - 2020",
             "bullets": [
                 "Built CI/CD pipelines in Jenkins and GitLab CI for 60 services.",
                 "Developed a Go-based deployment controller that automated blue/green releases.",
             ]},
        ],
        "education": ["B.S. Information Systems, Colorado State University, 2017"],
        "skills": {
            "Infrastructure": "Kubernetes, Helm, Terraform, Ansible, AWS, GCP",
            "Languages": "Python, Go, Bash",
            "Observability": "Prometheus, Grafana, OpenTelemetry, ELK",
            "CI/CD": "GitLab CI, Jenkins, ArgoCD",
        },
        "certifications": ["Certified Kubernetes Administrator (CKA)", "HashiCorp Certified: Terraform Associate"],
    },
    {
        "slug": "david_okafor", "fmt": "docx",
        "name": "David Okafor", "title": "Java Backend Developer",
        "contact": "david.okafor@example.com | +1 (555) 010-0186 | Chicago, IL | linkedin.com/in/example-dokafor",
        "summary": (
            "Backend developer with 9 years of experience delivering Java and Kotlin services for banking "
            "and insurance clients. Specialises in Spring Boot, messaging and relational database design."
        ),
        "experience": [
            {"role": "Lead Java Developer", "company": "Meridian Bank", "dates": "2019 - Present",
             "bullets": [
                 "Architected Spring Boot microservices processing 1.2M payment transactions per day.",
                 "Introduced Kafka event streaming, replacing nightly batch jobs with near-real-time updates.",
                 "Tuned Oracle and PostgreSQL queries, reducing report generation time by 70%.",
             ]},
            {"role": "Java Developer", "company": "Sentinel Insurance Tech", "dates": "2014 - 2019",
             "bullets": [
                 "Developed claims-processing services in Java 8 with Hibernate and JMS.",
                 "Migrated a legacy SOAP platform to RESTful APIs documented with OpenAPI.",
             ]},
        ],
        "education": ["B.Eng. Software Engineering, University of Illinois Chicago, 2014"],
        "skills": {
            "Languages": "Java, Kotlin, SQL, some Groovy",
            "Frameworks": "Spring Boot, Spring Cloud, Hibernate, JUnit, Mockito",
            "Platforms": "Kafka, RabbitMQ, Docker, Kubernetes, Jenkins",
            "Databases": "Oracle, PostgreSQL, MongoDB",
        },
        "certifications": ["Oracle Certified Professional, Java SE 11 Developer"],
    },
    {
        "slug": "sara_lindqvist", "fmt": "txt",
        "name": "Sara Lindqvist", "title": "Machine Learning Engineer (NLP)",
        "contact": "sara.lindqvist@example.com | +1 (555) 010-0121 | Boston, MA | github.com/example-slindqvist",
        "summary": (
            "ML engineer with 4 years of Python experience focused on natural language processing. "
            "Ships transformer-based search and classification systems from research prototype to production."
        ),
        "experience": [
            {"role": "Machine Learning Engineer", "company": "Verbatim AI", "dates": "2022 - Present",
             "bullets": [
                 "Fine-tuned Hugging Face transformer models in Python and PyTorch for document classification (F1 0.93).",
                 "Built a semantic-search service with sentence embeddings and FAISS serving 300 queries/second.",
                 "Created an evaluation harness in Python that catches regressions before each model release.",
             ]},
            {"role": "Research Engineer", "company": "Fjord Language Lab", "dates": "2020 - 2022",
             "bullets": [
                 "Trained TensorFlow and Keras sequence models for multilingual named-entity recognition.",
                 "Open-sourced a Python library for text-normalisation (1.8k GitHub stars).",
             ]},
        ],
        "education": [
            "M.Sc. Computational Linguistics, Uppsala University, 2020",
            "B.Sc. Computer Science, KTH Royal Institute of Technology, 2018",
        ],
        "skills": {
            "Languages": "Python, SQL, C++ (basic)",
            "ML / NLP": "PyTorch, TensorFlow, Hugging Face Transformers, spaCy, FAISS, scikit-learn",
            "MLOps": "MLflow, Docker, FastAPI, AWS SageMaker, GitHub Actions",
        },
        "certifications": ["DeepLearning.AI TensorFlow Developer Certificate (2021)"],
    },
    {
        "slug": "rahul_mehta", "fmt": "pdf",
        "name": "Rahul Mehta", "title": "Junior Software Developer (Recent Graduate)",
        "contact": "rahul.mehta@example.com | +1 (555) 010-0195 | Pittsburgh, PA | github.com/example-rmehta",
        "summary": (
            "Recent computer science graduate with hands-on Python experience from coursework, an "
            "internship and personal projects. Eager to grow as a backend or data engineer."
        ),
        "experience": [
            {"role": "Software Engineering Intern", "company": "Keystone Analytics", "dates": "Summer 2025",
             "bullets": [
                 "Built a Python (Flask) dashboard API used by the support team to track ticket backlogs.",
                 "Wrote pytest suites that raised coverage of the reporting module from 41% to 85%.",
             ]},
            {"role": "Teaching Assistant, Data Structures", "company": "Carnegie Mellon University", "dates": "2024 - 2025",
             "bullets": ["Ran weekly recitations and graded Python and Java assignments for 120 students."]},
        ],
        "education": ["B.S. Computer Science, Carnegie Mellon University, 2025 (GPA 3.7)"],
        "skills": {
            "Languages": "Python, Java, SQL, C",
            "Frameworks": "Flask, pandas, pytest",
            "Tools": "Git, Docker (basic), Linux, PostgreSQL",
        },
        "certifications": ["Python Institute PCEP (2024)"],
        "projects": [
            "Campus Ride-Share Matcher - Python/Flask app pairing students by route, 400+ active users.",
            "Stock Sentiment Tracker - Python script scoring headlines with NLTK and charting trends.",
        ],
    },
    {
        "slug": "emily_watson", "fmt": "docx",
        "name": "Emily Watson", "title": "Senior Product Manager",
        "contact": "emily.watson@example.com | +1 (555) 010-0164 | New York, NY | linkedin.com/in/example-ewatson",
        "summary": (
            "Product manager with 8 years of experience in B2B SaaS, specialising in analytics products "
            "and self-serve growth. Data-driven, SQL-fluent and focused on measurable customer outcomes."
        ),
        "experience": [
            {"role": "Senior Product Manager", "company": "Orbit Analytics", "dates": "2021 - Present",
             "bullets": [
                 "Own the reporting and dashboards roadmap used by 3,500 business customers.",
                 "Launched a self-serve onboarding flow that lifted activation from 31% to 47%.",
                 "Analysed funnel data with SQL and Looker; completed an introductory Python course (2024) to prototype cohort analyses.",
             ]},
            {"role": "Product Manager", "company": "Tandem Software", "dates": "2018 - 2021",
             "bullets": [
                 "Shipped a usage-based billing feature that generated $2.4M in new annual recurring revenue.",
                 "Ran 30+ customer discovery interviews per quarter to shape the integrations roadmap.",
             ]},
        ],
        "education": ["MBA, Columbia Business School, 2018", "B.A. Economics, Cornell University, 2013"],
        "skills": {
            "Product": "Roadmapping, user research, A/B testing, OKRs, pricing and packaging",
            "Analytics": "SQL, Looker, Tableau, Amplitude, Mixpanel",
            "Tools": "Jira, Figma, Productboard, Notion",
        },
        "certifications": ["Pragmatic Institute PMC-III (2022)"],
    },
]


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #

def _sections(r: Resume) -> list[tuple[str, list[str]]]:
    """Flatten a resume into (heading, lines) pairs shared by the TXT renderer."""
    out: list[tuple[str, list[str]]] = [("PROFESSIONAL SUMMARY", [r["summary"]])]
    exp: list[str] = []
    for job in r["experience"]:
        exp.append(f"{job['role']} - {job['company']} ({job['dates']})")
        exp.extend(f"  * {b}" for b in job["bullets"])
        exp.append("")
    out.append(("EXPERIENCE", exp[:-1]))
    if r.get("projects"):
        out.append(("PROJECTS", [f"  * {p}" for p in r["projects"]]))
    out.append(("EDUCATION", list(r["education"])))
    out.append(("SKILLS", [f"{k}: {v}" for k, v in r["skills"].items()]))
    out.append(("CERTIFICATIONS", [f"  * {c}" for c in r["certifications"]]))
    return out


def render_txt(r: Resume, path: Path) -> None:
    lines = [r["name"].upper(), r["title"], r["contact"], ""]
    for heading, body in _sections(r):
        lines += [heading, "-" * len(heading), *body, ""]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def render_pdf(r: Resume, path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    name = ParagraphStyle("Name", parent=styles["Title"], fontSize=22, spaceAfter=2)
    sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=11, textColor=colors.HexColor("#444444"), alignment=1)
    h = ParagraphStyle("H", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=2,
                       textColor=colors.HexColor("#1F3A5F"))
    body = styles["BodyText"]
    job_style = ParagraphStyle("Job", parent=body, fontName="Helvetica-Bold", spaceBefore=4)

    def bullets(items: list[str]) -> ListFlowable:
        return ListFlowable([ListItem(Paragraph(i, body), leftIndent=14) for i in items], bulletType="bullet",
                            leftIndent=14, bulletFontSize=6)

    def heading(text: str) -> list[Any]:
        return [Paragraph(text, h), HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#1F3A5F"))]

    flow: list[Any] = [Paragraph(r["name"], name), Paragraph(r["title"], sub), Paragraph(r["contact"], sub), Spacer(1, 6)]
    flow += heading("Professional Summary") + [Paragraph(r["summary"], body)]
    flow += heading("Experience")
    for job in r["experience"]:
        flow.append(Paragraph(f"{job['role']} - {job['company']} ({job['dates']})", job_style))
        flow.append(bullets(job["bullets"]))
    if r.get("projects"):
        flow += heading("Projects") + [bullets(r["projects"])]
    flow += heading("Education") + [Paragraph(e, body) for e in r["education"]]
    flow += heading("Skills") + [Paragraph(f"<b>{k}:</b> {v}", body) for k, v in r["skills"].items()]
    flow += heading("Certifications") + [bullets(r["certifications"])]

    SimpleDocTemplate(
        str(path), pagesize=LETTER, title=f"Resume - {r['name']}", author=r["name"],
        leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.7 * inch, bottomMargin=0.7 * inch,
    ).build(flow)


def render_docx(r: Resume, path: Path) -> None:
    import docx
    from docx.shared import Pt

    d = docx.Document()
    d.core_properties.title = f"Resume - {r['name']}"
    d.core_properties.author = r["name"]
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(10.5)

    d.add_heading(r["name"], level=0)
    d.add_paragraph(r["title"]).runs[0].bold = True
    d.add_paragraph(r["contact"])

    d.add_heading("Professional Summary", level=1)
    d.add_paragraph(r["summary"])

    d.add_heading("Experience", level=1)
    for job in r["experience"]:
        p = d.add_paragraph()
        p.add_run(f"{job['role']} - {job['company']}").bold = True
        p.add_run(f"  ({job['dates']})")
        for b in job["bullets"]:
            d.add_paragraph(b, style="List Bullet")

    if r.get("projects"):
        d.add_heading("Projects", level=1)
        for p_ in r["projects"]:
            d.add_paragraph(p_, style="List Bullet")

    d.add_heading("Education", level=1)
    for e in r["education"]:
        d.add_paragraph(e)

    d.add_heading("Skills", level=1)
    table = d.add_table(rows=0, cols=2)  # a real table, so read_file's table extraction is exercised
    table.style = "Light Grid Accent 1"
    for k, v in r["skills"].items():
        cells = table.add_row().cells
        cells[0].text, cells[1].text = k, v

    d.add_heading("Certifications", level=1)
    for c in r["certifications"]:
        d.add_paragraph(c, style="List Bullet")
    d.save(str(path))


_RENDERERS = {"txt": render_txt, "pdf": render_pdf, "docx": render_docx}


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for r in RESUMES:
        path = OUT_DIR / f"resume_{r['slug']}.{r['fmt']}"
        _RENDERERS[r["fmt"]](r, path)
        print(f"wrote {path.relative_to(OUT_DIR.parent)}  ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
