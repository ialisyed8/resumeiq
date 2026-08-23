"""
Every endpoint must carry authentication and a rate-limit tier.

This is a structural test rather than a behavioural one: it reads the route
signatures and asserts that each one declares a protected principal type. The
point is that a new endpoint cannot ship unprotected by omission — the failure
happens at test time, not in production when someone finds the hole.

The original audit found `limit_requests()` defined with zero call sites. This
test exists so that cannot recur.
"""
import ast
import pathlib

import pytest

ROUTES = pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "v1"

#: Aliases that apply both authentication and a rate-limit tier.
TIERED = {"ReadUser", "WriteUser", "UploadUser", "AiUser"}
#: Authenticated but deliberately untiered — must be cheap and justified below.
UNTIERED_ALLOWED = {
    "logout": "single call, revokes a token, no expensive work",
    "me": "session identity lookup, called once per page load",
}
#: Unauthenticated by necessity. Each must rate limit internally via limit_auth.
PUBLIC_ALLOWED = {
    "register", "login", "refresh_tokens", "forgot_password", "reset_password",
}


def _endpoints():
    for path in sorted(ROUTES.glob("*.py")):
        if path.name == "__init__.py":
            continue
        src = path.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            decorators = [ast.get_source_segment(src, d) or "" for d in node.decorator_list]
            if not any(d.startswith("router.") for d in decorators):
                continue
            annotations = " ".join(
                ast.get_source_segment(src, a.annotation) or ""
                for a in node.args.args if a.annotation
            )
            body = ast.get_source_segment(src, node) or ""
            yield path.name, node.name, annotations, body


ENDPOINTS = list(_endpoints())


def test_endpoints_were_discovered():
    """Guard against the parser silently finding nothing and passing vacuously."""
    assert len(ENDPOINTS) >= 30, f"only found {len(ENDPOINTS)} endpoints"


@pytest.mark.parametrize(
    "module,name,annotations,body", ENDPOINTS,
    ids=[f"{m}::{n}" for m, n, _, _ in ENDPOINTS],
)
def test_endpoint_is_authenticated(module, name, annotations, body):
    if name in PUBLIC_ALLOWED:
        pytest.skip("deliberately unauthenticated")
    authed = any(
        alias in annotations
        for alias in TIERED | {"CurrentUser", "AdminUser"}
    )
    assert authed, f"{module}::{name} accepts no authenticated principal"


@pytest.mark.parametrize(
    "module,name,annotations,body", ENDPOINTS,
    ids=[f"{m}::{n}" for m, n, _, _ in ENDPOINTS],
)
def test_endpoint_is_rate_limited(module, name, annotations, body):
    """
    Authenticated endpoints carry a tier; public ones call limit_auth directly.
    """
    if name in PUBLIC_ALLOWED:
        assert "limit_auth" in body, (
            f"{module}::{name} is public and does not rate limit — this is the "
            "credential-stuffing surface"
        )
        return
    if name in UNTIERED_ALLOWED:
        pytest.skip(UNTIERED_ALLOWED[name])
    assert any(alias in annotations for alias in TIERED), (
        f"{module}::{name} has no rate-limit tier. Use ReadUser, WriteUser, "
        f"UploadUser or AiUser rather than CurrentUser."
    )


def test_expensive_operations_use_the_ai_tier():
    """
    Endpoints that spend money must carry the AI tier and a budget check.

    Rate limiting alone does not bound spend — 10 screenings a minute is within
    the AI tier and still unbounded over a month.
    """
    expensive = {"create_job", "start_screening"}
    seen = set()
    for module, name, annotations, body in ENDPOINTS:
        if name not in expensive:
            continue
        seen.add(name)
        assert "AiUser" in annotations, f"{name} must use the AI rate-limit tier"
        assert "enforce_budget" in body, f"{name} must check the org AI budget"
    assert seen == expensive, f"expected endpoints not found: {expensive - seen}"


def test_no_endpoint_uses_the_bare_limiter():
    """
    The audit found `limit_requests` defined and never called. Tiered dependencies
    replaced it; nothing should reach for the raw helper again.
    """
    for module, name, _, body in ENDPOINTS:
        assert "limit_requests(" not in body, (
            f"{module}::{name} calls limit_requests directly — use a tiered "
            "dependency so the limit cannot be forgotten on the next endpoint"
        )
