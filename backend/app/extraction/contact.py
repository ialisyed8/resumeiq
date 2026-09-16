"""
Contact detail extraction.

Deliberately regex-based rather than model-based. Two reasons:

1. **It keeps identity out of the model path entirely.** The extraction prompt in
   ai/prompts.py explicitly refuses to return names, emails, or phone numbers, so
   that identifying data cannot leak into the structured profile the scorer
   reads. Extracting them here, separately and locally, preserves that guarantee
   instead of quietly undoing it.

2. **It is more reliable for this task.** Emails and phone numbers have regular
   shapes. A name on a resume is almost always the first substantial line, set
   apart from everything else. A model adds cost and variance for no accuracy.

The output of this module is written to `candidate_identities`, which the worker
database role can INSERT into but not SELECT from (migration 0002). Scoring
therefore cannot read it back even by accident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

#: Deliberately permissive — international formats vary enormously and a
#: false positive here is harmless, while a miss means a recruiter has to open
#: the PDF to find a phone number.
PHONE = re.compile(
    r"(?:(?<=^)|(?<=[\s(|·•,]))"
    r"(?:\+\d{1,3}[\s.-]?)?"
    r"(?:\(\d{1,4}\)[\s.-]?)?"
    r"\d{2,4}(?:[\s.-]\d{2,4}){1,4}"
    r"(?=$|[\s)|·•,])"
)

LINK = re.compile(
    r"\b((?:https?://)?(?:www\.)?"
    r"(?:linkedin\.com/in/|github\.com/|gitlab\.com/)"
    r"[A-Za-z0-9._/-]+)",
    re.IGNORECASE,
)

#: Lines matching these are headings or contact rows, never the candidate's name.
NOT_A_NAME = re.compile(
    r"(curriculum vitae|resume|r[ée]sum[ée]|profile|summary|contact|"
    r"personal details|@|\d{3}|http|www\.)",
    re.IGNORECASE,
)

#: Job titles frequently sit on line two. A name should not look like one.
TITLE_WORDS = frozenset(
    ["engineer", "developer", "manager", "director", "consultant", "analyst", "designer", "architect", "specialist", "lead", "senior", "junior", "principal", "staff", "head", "officer", "intern", "associate", "scientist", "administrator", "coordinator", "executive"]
)

#: Section headings are often two capitalised words and would otherwise pass the
#: name test — labelling a candidate "Professional Experience" is worse than
#: failing to find a name at all.
SECTION_WORDS = frozenset(
    ["experience", "education", "skills", "profile", "summary", "projects", "certifications", "employment", "references", "achievements", "publications", "languages", "interests", "contact", "objective", "technical", "professional", "work", "career", "qualifications", "awards", "training", "background", "competencies", "expertise", "portfolio", "accomplishments"]
)

#: Name particles that are conventionally lowercase. Without these, names such
#: as "Joris van der Berg" or "Maria da Silva" fail a naive capitalisation test.
PARTICLES = frozenset(
    ["van", "der", "den", "de", "di", "da", "do", "dos", "das", "del", "della", "la", "le", "du", "bin", "ibn", "al", "el", "von", "zu", "ter", "ten", "af", "av", "mac", "mc", "o'", "st"]
)


def _looks_like_a_name(line: str) -> bool:
    """
    Heuristic for the candidate's name.

    Two to five words, no digits, no job-title or section-heading vocabulary,
    mostly capitalised once conventional lowercase particles are discounted.
    Deliberately conservative: returning None is a better failure than labelling
    a candidate with a section heading.
    """
    stripped = line.strip(" .,|·•-–—\t")
    if not (3 <= len(stripped) <= 60):
        return False
    if NOT_A_NAME.search(stripped) or any(c.isdigit() for c in stripped):
        return False
    # "Skills: Python" and similar are labelled fields, never names.
    if ":" in stripped:
        return False

    words = stripped.split()
    if not (2 <= len(words) <= 5):
        return False

    lowered = [w.lower().strip(",.") for w in words]
    if any(w in TITLE_WORDS for w in lowered):
        return False
    if any(w in SECTION_WORDS for w in lowered):
        return False

    # Count capitalisation only among words that are not conventional particles.
    substantive = [w for w, low in zip(words, lowered, strict=False) if low not in PARTICLES]
    if len(substantive) < 2:
        return False
    capitalised = sum(1 for w in substantive if w[:1].isupper())
    return capitalised == len(substantive)


@dataclass(slots=True)
class ContactDetails:
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    links: dict[str, str] | None = None

    def as_kwargs(self) -> dict:
        return {
            "full_name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "links": self.links or {},
        }


def extract_contact(text: str, *, search_lines: int = 12) -> ContactDetails:
    """
    Pull contact details from the top of a resume.

    Only the first `search_lines` non-empty lines are considered for the name,
    because a name appearing further down is far more likely to be a referee, a
    colleague, or a project collaborator than the candidate.
    """
    details = ContactDetails()
    if not text or not text.strip():
        return details

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = "\n".join(lines[:search_lines])

    email_match = EMAIL.search(head) or EMAIL.search(text)
    if email_match:
        details.email = email_match.group(0)

    for candidate in PHONE.finditer(head):
        raw = candidate.group(0).strip()
        digits = sum(c.isdigit() for c in raw)
        # Long enough to be a phone number, short enough not to be a date range
        # or a metric like "3.1s to 1.2s".
        if 7 <= digits <= 15:
            details.phone = raw
            break

    links: dict[str, str] = {}
    for match in LINK.finditer(head):
        url = match.group(1)
        host = "linkedin" if "linkedin" in url.lower() else (
            "github" if "github" in url.lower() else "gitlab"
        )
        links.setdefault(host, url)
    if links:
        details.links = links

    for line in lines[:search_lines]:
        if _looks_like_a_name(line):
            details.full_name = line.strip(" .,|·•-–—\t")
            break

    return details
