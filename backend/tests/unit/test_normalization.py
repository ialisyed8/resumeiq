"""Skill normalisation: the deterministic layer that carries most of the accuracy."""
import pytest

from app.matching.normalization import (
    aliases_for,
    canonicalise_text,
    expand_with_implications,
    find_skills,
    normalise_all,
    normalise_skill,
)


class TestCanonicalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("React.js", "React"), ("reactjs", "React"), ("REACT 18", "React"),
        ("k8s", "Kubernetes"), ("K8S", "Kubernetes"),
        ("postgres", "PostgreSQL"), ("Postgres", "PostgreSQL"),
        ("aws cloud", "AWS"), ("Amazon Web Services", "AWS"),
        ("TS", "TypeScript"), ("golang", "Go"),
        ("nodejs", "Node.js"), ("node", "Node.js"),
        ("wcag", "Accessibility (WCAG)"), ("a11y", "Accessibility (WCAG)"),
        ("storybook", "Design systems"),
        ("playwright", "Testing (unit + E2E)"),
    ])
    def test_aliases_map_to_canonical(self, raw, expected):
        assert normalise_skill(raw) == expected

    def test_unknown_returns_none(self):
        assert normalise_skill("Underwater Basket Weaving") is None
        assert normalise_skill("") is None

    def test_accents_stripped(self):
        assert canonicalise_text("Café") == "cafe"

    def test_deduplicates(self):
        result = normalise_all(["React", "react.js", "ReactJS", "TypeScript"])
        assert result == ["React", "TypeScript"]

    def test_aliases_for_surfaces_search_terms(self):
        terms = aliases_for("Accessibility (WCAG)")
        assert "wcag" in terms and "aria" in terms
        assert terms[0] == "Accessibility (WCAG)"


class TestTextScanning:
    def test_finds_skills_with_offsets(self):
        text = "Built the checkout in React 18 with TypeScript."
        result = find_skills(text)
        canon = result.canonical_skills
        assert "React" in canon and "TypeScript" in canon
        match = result.for_skill("React")[0]
        assert text[match.start:match.end].lower().startswith("react")

    def test_longest_alias_wins(self):
        """'Next.js' must not be shredded into a bare match."""
        result = find_skills("Deployed with Next.js on Vercel")
        assert "Next.js" in result.canonical_skills

    def test_no_substring_false_positives(self):
        """'Go' must not match inside 'Django' or 'going'."""
        result = find_skills("Django developer, going to production")
        assert "Go" not in result.canonical_skills

    def test_word_boundaries(self):
        assert "React" not in find_skills("Reactive programming").canonical_skills

    def test_empty_text(self):
        assert find_skills("").canonical_skills == []

    def test_hyphen_and_underscore_variants(self):
        assert "Node.js" in find_skills("node-js experience").canonical_skills


class TestImplications:
    def test_nextjs_implies_react_as_proxy(self):
        proxies = expand_with_implications(["Next.js"])
        implied = {p.canonical for p in proxies}
        assert "React" in implied
        assert all(p.is_proxy for p in proxies)

    def test_no_proxy_when_already_present(self):
        proxies = expand_with_implications(["Next.js", "React"])
        assert "React" not in {p.canonical for p in proxies}

    def test_kubernetes_implies_docker(self):
        assert "Docker" in {p.canonical for p in expand_with_implications(["Kubernetes"])}
