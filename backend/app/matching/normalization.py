"""
Deterministic skill normalisation.

This layer is unglamorous and it carries most of the accuracy. An alias
dictionary that knows React == React.js == ReactJS == React 18 outperforms a
mediocre embedding model on exactly the cases recruiters care about, costs
nothing per query, and is auditable — a recruiter can be shown the precise list
of terms that were searched for.

Seed the taxonomy from ESCO (ec.europa.eu/esco, ~13.9k skills, multilingual) or
O*NET (onetcenter.org, US public domain) via scripts/import_taxonomy.py, then
extend with the internal aliases below. Nothing here calls a model.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

#: Canonical skill -> aliases. Extend freely; entries are matched case- and
#: punctuation-insensitively. Keep the canonical form as the recruiter-facing
#: spelling.
SKILL_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    # Frontend
    "React": ("react.js", "reactjs", "react js", "react 16", "react 17", "react 18", "react19", "react 19"),
    "Next.js": ("nextjs", "next js", "next.js 13", "next 14", "nextjs 13"),
    "Vue.js": ("vue", "vuejs", "vue 3", "vue.js 3"),
    "Angular": ("angularjs", "angular 2+", "angular js"),
    "Svelte": ("sveltekit", "svelte kit"),
    "TypeScript": ("ts", "type script"),
    "JavaScript": ("js", "ecmascript", "es6", "es2015", "es2020", "vanilla js"),
    "Tailwind CSS": ("tailwind", "tailwindcss"),
    "Redux": ("redux toolkit", "rtk"),
    "Webpack": ("web pack",),
    "Vite": ("vitejs", "vite.js"),
    # Backend / languages
    "Python": ("python3", "python 3", "py"),
    "Node.js": ("node", "nodejs", "node js"),
    "Go": ("golang",),
    "C#": ("c sharp", "csharp", "dotnet c#"),
    ".NET": ("dotnet", "dot net", ".net core", "asp.net"),
    "Java": ("java 8", "java 11", "java 17", "core java"),
    "Ruby on Rails": ("rails", "ror", "ruby-on-rails"),
    "FastAPI": ("fast api",),
    "Django": ("django rest framework", "drf"),
    "Spring Boot": ("springboot", "spring-boot", "spring"),
    # Data
    "PostgreSQL": ("postgres", "psql", "postgresql 14", "postgre sql"),
    "MySQL": ("my sql", "mariadb"),
    "MongoDB": ("mongo", "mongo db"),
    "Redis": ("redis cache",),
    "Elasticsearch": ("elastic search", "elk", "opensearch"),
    "Apache Kafka": ("kafka",),
    "Apache Spark": ("spark", "pyspark"),
    "Snowflake": ("snowflake dw",),
    # Cloud / infra
    "AWS": ("amazon web services", "aws cloud", "ec2", "s3", "lambda", "eks", "amazon aws"),
    "Azure": ("microsoft azure", "azure cloud", "aks"),
    "Google Cloud": ("gcp", "google cloud platform", "gke"),
    "Kubernetes": ("k8s", "k8", "kube", "eks", "gke", "aks"),
    "Docker": ("containerisation", "containerization", "docker compose", "containers"),
    "Terraform": ("terraform cloud", "hcl", "iac terraform"),
    "CI/CD": ("continuous integration", "continuous delivery", "continuous deployment", "ci cd", "cicd"),
    "GitHub Actions": ("gh actions", "github action"),
    # Practice
    "GraphQL": ("apollo", "apollo client", "apollo server", "relay", "graph ql"),
    "REST API": ("rest", "restful", "restful api", "rest apis"),
    "Accessibility (WCAG)": (
        "wcag", "a11y", "aria", "web accessibility", "screen reader",
        "wcag 2.1", "wcag 2.2", "section 508", "accessible design", "vpat",
    ),
    "Testing (unit + E2E)": (
        "jest", "vitest", "playwright", "cypress", "testing library",
        "unit testing", "end-to-end testing", "e2e testing", "pytest", "mocha",
    ),
    "Design systems": (
        "design system", "component library", "storybook", "pattern library",
        "polaris", "material ui", "mui", "chakra ui", "design tokens",
    ),
    "Performance at scale": (
        "core web vitals", "lighthouse", "bundle size", "web performance",
        "lcp", "performance optimisation", "performance optimization", "tti",
    ),
    "Team leadership": (
        "tech lead", "technical lead", "mentoring", "mentored", "line management",
        "team lead", "engineering manager", "led a team", "people management",
    ),
    "Agile": ("scrum", "kanban", "agile delivery", "sprint planning", "safe"),
    # ML
    "Machine Learning": ("ml", "machine-learning"),
    "PyTorch": ("torch", "py torch"),
    "TensorFlow": ("tf", "tensor flow", "keras"),
    "scikit-learn": ("sklearn", "scikit learn"),
    "Large Language Models": ("llm", "llms", "genai", "generative ai", "gpt", "rag"),
}

#: Implication edges: having the key is strong evidence for the value, but not
#: the same thing. Matches here are graded STRONG_PROXY (0.4), never MET.
SKILL_IMPLICATIONS: Final[dict[str, tuple[str, ...]]] = {
    "Next.js": ("React",),
    "Redux": ("React",),
    "SvelteKit": ("Svelte",),
    "Kubernetes": ("Docker",),
    "Terraform": ("CI/CD",),
    "Apollo": ("GraphQL",),
    "Django": ("Python",),
    "FastAPI": ("Python",),
    "Rails": ("Ruby",),
    "Spring Boot": ("Java",),
    "PySpark": ("Python", "Apache Spark"),
}

_PUNCT = re.compile(r"[^\w\s+#.]")
_WS = re.compile(r"\s+")


def canonicalise_text(value: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = stripped.lower().strip()
    cleaned = _PUNCT.sub(" ", lowered)
    return _WS.sub(" ", cleaned).strip()


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    """Reverse index: normalised alias -> canonical skill."""
    index: dict[str, str] = {}
    for canonical, aliases in SKILL_ALIASES.items():
        index[canonicalise_text(canonical)] = canonical
        for alias in aliases:
            index[canonicalise_text(alias)] = canonical
    return index


def normalise_skill(raw: str) -> str | None:
    """Map a raw skill mention to its canonical name, or None if unknown."""
    key = canonicalise_text(raw)
    if not key:
        return None
    return _alias_index().get(key)


def normalise_all(values: Iterable[str]) -> list[str]:
    """Normalise a collection, dropping unknowns and de-duplicating."""
    seen: dict[str, None] = {}
    for value in values:
        canonical = normalise_skill(value)
        if canonical:
            seen.setdefault(canonical, None)
    return list(seen)


def aliases_for(canonical: str) -> list[str]:
    """
    Every search term for a skill.

    Surfaced in the UI under a missing requirement so the recruiter can see
    exactly what was looked for — 'No mention of WCAG, ARIA, screen readers,
    or audits' rather than an unexplained zero.
    """
    if canonical not in SKILL_ALIASES:
        return [canonical]
    return [canonical, *SKILL_ALIASES[canonical]]


@dataclass(slots=True)
class SkillMatch:
    canonical: str
    matched_text: str
    start: int
    end: int
    is_proxy: bool = False
    implied_by: str | None = None


@dataclass(slots=True)
class MatchResult:
    matches: list[SkillMatch] = field(default_factory=list)

    @property
    def canonical_skills(self) -> list[str]:
        return list({m.canonical: None for m in self.matches})

    def for_skill(self, canonical: str) -> list[SkillMatch]:
        return [m for m in self.matches if m.canonical == canonical]


@lru_cache(maxsize=1)
def _match_patterns() -> list[tuple[re.Pattern[str], str]]:
    """
    Compile one word-boundary pattern per alias, longest first.

    Longest-first avoids 'Go' inside 'Django' and 'React' inside 'React Native'
    stealing the more specific match.
    """
    entries: list[tuple[str, str]] = []
    for canonical, aliases in SKILL_ALIASES.items():
        entries.append((canonical, canonical))
        for alias in aliases:
            entries.append((alias, canonical))
    entries.sort(key=lambda e: len(e[0]), reverse=True)

    compiled: list[tuple[re.Pattern[str], str]] = []
    for surface, canonical in entries:
        escaped = re.escape(surface).replace(r"\ ", r"[\s\-_]+")
        compiled.append(
            (re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE), canonical)
        )
    return compiled


def find_skills(text: str) -> MatchResult:
    """
    Locate every known skill in a block of text, with character offsets.

    Offsets are kept because the UI highlights the matched span in the source
    document, and because evidence validation checks quotes against the original
    text rather than trusting the model.
    """
    result = MatchResult()
    if not text:
        return result

    claimed: list[tuple[int, int]] = []
    for pattern, canonical in _match_patterns():
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if any(span[0] < e and s < span[1] for s, e in claimed):
                continue  # already covered by a longer alias
            claimed.append(span)
            result.matches.append(
                SkillMatch(
                    canonical=canonical,
                    matched_text=match.group(0),
                    start=span[0],
                    end=span[1],
                )
            )

    result.matches.sort(key=lambda m: m.start)
    return result


def expand_with_implications(skills: Iterable[str]) -> list[SkillMatch]:
    """
    Add proxy matches implied by the skills present.

    Someone who lists Next.js almost certainly knows React, but the resume does
    not say so. These are graded as proxies and never satisfy a must-have on
    their own.
    """
    present = set(skills)
    proxies: list[SkillMatch] = []
    for source, implied_list in SKILL_IMPLICATIONS.items():
        canonical_source = normalise_skill(source) or source
        if canonical_source not in present:
            continue
        for implied in implied_list:
            canonical_implied = normalise_skill(implied) or implied
            if canonical_implied in present:
                continue
            proxies.append(
                SkillMatch(
                    canonical=canonical_implied,
                    matched_text=canonical_source,
                    start=-1,
                    end=-1,
                    is_proxy=True,
                    implied_by=canonical_source,
                )
            )
    return proxies
