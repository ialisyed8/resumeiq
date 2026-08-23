"""
Contact extraction.

Identity is deliberately extracted without a model, so these tests are the only
thing standing between a recruiter and a candidate labelled "Curriculum Vitae".
"""
import pytest

from app.extraction.contact import extract_contact

PRIYA = """Priya Raman
Senior Backend Engineer
priya.raman@example.com  ·  +1 (415) 555-0142  ·  San Francisco, CA  ·  github.com/praman-dev

SUMMARY
Backend engineer with nine years building Python services at scale.
"""


class TestNameExtraction:
    def test_finds_name_on_first_line(self):
        assert extract_contact(PRIYA).full_name == "Priya Raman"

    def test_skips_document_heading(self):
        text = "CURRICULUM VITAE\n\nDaniel Okonkwo\nPlatform Engineer\nd@example.com"
        assert extract_contact(text).full_name == "Daniel Okonkwo"

    def test_does_not_mistake_a_job_title_for_a_name(self):
        text = "Senior Backend Engineer\nAlina Costa\nalina@example.com"
        assert extract_contact(text).full_name == "Alina Costa"

    def test_handles_lowercase_particles(self):
        text = "Joris van der Berg\nEngineer\nj@example.com"
        assert extract_contact(text).full_name == "Joris van der Berg"

    def test_returns_none_rather_than_guessing_badly(self):
        text = "PROFESSIONAL EXPERIENCE\n2019 - 2024 Various roles\nSkills: Python"
        assert extract_contact(text).full_name is None

    def test_ignores_names_further_down_the_document(self):
        """A name deep in the text is a referee or colleague, not the candidate."""
        text = "SKILLS\nPython\n" + "\n" * 3 + "x\n" * 20 + "Marcus Feld\n"
        assert extract_contact(text).full_name is None

    def test_empty_input(self):
        assert extract_contact("").full_name is None


class TestEmailAndPhone:
    def test_email(self):
        assert extract_contact(PRIYA).email == "priya.raman@example.com"

    @pytest.mark.parametrize("raw", [
        "+1 (415) 555-0142", "+44 20 7946 0221", "+351 21 555 0177",
        "(206) 555-0198", "415-555-0142",
    ])
    def test_international_phone_formats(self, raw):
        text = f"Priya Raman\nEngineer\np@example.com · {raw} · London"
        assert extract_contact(text).phone is not None

    def test_does_not_read_a_date_range_as_a_phone_number(self):
        text = "Priya Raman\nEngineer\np@example.com\nEXPERIENCE\n2019 - 2024"
        contact = extract_contact(text)
        assert contact.phone is None or "2019" not in contact.phone

    def test_no_contact_details_present(self):
        contact = extract_contact("SKILLS\nPython, SQL\nEXPERIENCE\nVarious")
        assert contact.email is None and contact.phone is None


class TestLinks:
    def test_github(self):
        assert extract_contact(PRIYA).links == {"github": "github.com/praman-dev"}

    def test_linkedin(self):
        text = "Ana Silva\nEngineer\na@example.com · linkedin.com/in/anasilva"
        assert "linkedin" in (extract_contact(text).links or {})


class TestPrivacyBoundary:
    def test_output_carries_only_contact_fields(self):
        """
        This module must never return anything the scorer could use. If a field
        is added here, it has to be one a recruiter reveals deliberately.
        """
        contact = extract_contact(PRIYA)
        assert set(contact.as_kwargs()) == {"full_name", "email", "phone", "links"}
