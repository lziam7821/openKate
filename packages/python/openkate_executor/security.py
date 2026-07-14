import re
from copy import deepcopy
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse

from fastapi import HTTPException

TEMPLATE_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*}}")
SENSITIVE_KEYS = {"authorization", "cookie", "set-cookie", "password", "secret", "token", "api_key", "apikey", "access_token", "accesstoken", "refresh_token", "refreshtoken"}


def value_at_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(path)
    return current


def render_templates(value: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [render_templates(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: render_templates(item, variables) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    exact = TEMPLATE_PATTERN.fullmatch(value)
    if exact:
        try:
            return value_at_path(variables, exact.group(1))
        except KeyError as error:
            raise HTTPException(status_code=422, detail=f"missing variable: {error.args[0]}") from error

    def replace(match: re.Match[str]) -> str:
        try:
            return str(value_at_path(variables, match.group(1)))
        except KeyError as error:
            raise HTTPException(status_code=422, detail=f"missing variable: {error.args[0]}") from error

    return TEMPLATE_PATTERN.sub(replace, value)


def redact(value: Any) -> Any:
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "***" if key.lower() in SENSITIVE_KEYS or key.lower().endswith(("password", "secret", "token", "api-key", "apikey")) else redact(item)
            for key, item in value.items()
        }
    return deepcopy(value)


def assert_allowed_url(url: str, allowed_hosts: Iterable[str]) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="executor URL must use HTTP or HTTPS")
    if parsed.hostname not in set(allowed_hosts):
        raise HTTPException(status_code=403, detail=f"host is not in the project allowlist: {parsed.hostname}")


def evaluate_assertions(actual: Any, assertions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for assertion in assertions:
        path = str(assertion.get("path", ""))
        operator = assertion.get("operator", "equals")
        try:
            observed = value_at_path(actual, path) if path else actual
            if operator == "equals":
                passed = observed == assertion.get("expected")
            elif operator == "exists":
                passed = observed is not None
            elif operator == "contains":
                passed = assertion.get("expected") in observed
            else:
                raise HTTPException(status_code=422, detail=f"unsupported assertion operator: {operator}")
        except (KeyError, IndexError, TypeError):
            observed, passed = None, False
        results.append({"path": path, "operator": operator, "expected": redact(assertion.get("expected")), "observed": redact(observed), "passed": passed})
    return results
