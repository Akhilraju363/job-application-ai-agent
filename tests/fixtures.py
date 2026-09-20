"""Shared test fixtures for the dashboard tests: a self-contained master resume (two
employers with deliberately different stacks, like the real one) and helpers.

Stdlib only -- run everything with: python -m unittest discover -s tests -v
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

# Hermetic: the scripts call load_dotenv() at import time, which would copy the developer's
# real .env (API keys, llm_base_url...) into os.environ for the whole test process -- both
# reading real secrets in tests and leaking into the existing tests' env handling (on Windows
# env names are upper-cased, which defeats test_llm's `startswith("llm_")` filter).
# Must be set before any script module is imported, so every dashboard test imports this first.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

BASE = """# Jane Roe
Software Engineer | Java | Spring Boot | Angular

jane@example.com | Bengaluru, India

## Summary
Software Engineer with 4+ years of experience building Java and Angular applications. Experienced in healthcare and REST API design.

## Skills
- Languages: Java (Core & Advanced), Python, TypeScript, SQL
- Frameworks: Spring Boot, Hibernate, Angular, RxJS, FastAPI
- Architecture: Microservices, RESTful APIs
- Databases: MySQL, PostgreSQL
- Cloud & DevOps: AWS, Docker
- Tools: Git, SCSS

## Experience

### Software Engineer | Python | Angular — Zyter Technologies
Jan 2026 - Present | Bengaluru, India
- Developed Python backend services and RESTful APIs using FastAPI for healthcare workflows.
- Built reusable Angular components and integrated them with Python backend APIs.
- Worked with PostgreSQL databases for API-driven data processing.

### Software Engineer | Java | Angular | Spring Boot — Infinite Computer Solutions
Feb 2025 - Dec 2025 | Bengaluru, India
- Developed Java backend services and RESTful APIs using Spring Boot.
- Implemented reusable Angular components with RxJS integrated with Java backend APIs.
- Worked with microservices architecture for scalable application development.

### Software Engineer | Java | Microservices — LTIMindtree
Jan 2022 - Jun 2024 | Chennai, India
- Designed RESTful APIs and microservices using Java, Spring Boot and Hibernate.
- Optimized MySQL database performance through indexing and query optimization.
- Contributed to CI/CD and deployment workflows using Docker and AWS.

## Education
Bachelor of Science — Example University, 2015 - 2018

## Certifications
- AWS Certified Cloud Practitioner
"""

JD_TEXT = ("We are hiring a Full Stack Java Developer with 3+ years of experience. You will build REST APIs "
           "with Spring Boot and Angular, work with MySQL and Docker, and ship on AWS. Kafka experience is "
           "required. Nice to have: Kubernetes. " * 2)

ANALYSIS = {
    "required_skills": ["Java", "Spring Boot", "Angular", "Kafka"],
    "preferred_skills": ["Kubernetes"],
    "programming_languages": ["Java"],
    "frameworks": ["Spring Boot", "Angular"],
    "databases": ["MySQL"],
    "cloud": ["AWS"],
    "tools": ["Docker", "Kafka", "Kubernetes"],
    "technologies": ["Java", "Spring Boot", "Angular", "MySQL", "Docker", "AWS", "Kafka", "Kubernetes"],
    "responsibilities": ["Build REST APIs", "Ship on AWS"],
    "experience_requirements": ["3+ years"],
    "experience_years_min": 3,
    "experience_notes": "3+ years",
    "domain": "Enterprise software",
    "keywords": ["REST APIs", "microservices", "Agile"],
    "education": [],
    "certifications": [],
}


class TempOutput:
    """Point paths.OUTPUT_DIR at a fresh temp dir for the duration of a test."""

    def __enter__(self):
        import paths
        self.dir = Path(tempfile.mkdtemp(prefix="jobagent-test-"))
        self._patch = mock.patch.object(paths, "OUTPUT_DIR", self.dir)
        self._patch.start()
        return self.dir

    def __exit__(self, *exc):
        self._patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)


def reorder_bullets(md):
    """A faithful 'tailoring': same content, bullets within each role reversed."""
    out, bullets = [], []
    for line in md.splitlines():
        if line.startswith("- ") and out and any(l.startswith("### ") for l in out) and \
                not any(l.startswith("## Education") for l in out):
            bullets.append(line)
            continue
        out.extend(reversed(bullets))
        bullets = []
        out.append(line)
    out.extend(reversed(bullets))
    return "\n".join(out) + "\n"
