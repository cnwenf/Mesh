"""Trigger filter matching (autopilot.md §2.6 / P4).

Dimensions combine with AND; multiple values inside ONE dimension combine
with OR (e.g. priorities ``["high","critical"]`` matches either). An empty
or absent dimension imposes no constraint.

``payload_match`` entries are ``{"path", "op", "value"}`` rules evaluated
against the trigger payload (webhook bodies / event data), AND-combined:

* ``eq`` / ``neq`` — deep equality after JSON normalization;
* ``in`` / ``not_in`` — membership in a list value;
* ``contains`` — substring (strings) or element membership (lists);
* ``exists`` — truthiness of ``value`` decides presence/absence;
* ``gt`` / ``lt`` — numeric comparison.
"""

from __future__ import annotations

from typing import Any

from mesh.autopilot.template import _resolve_path

PAYLOAD_OPS = ("eq", "neq", "in", "not_in", "contains", "exists", "gt", "lt")


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def match_payload_rules(rules: Any, payload: dict[str, Any]) -> bool:
    """Evaluate ``payload_match`` rules (AND across rules)."""
    if not rules:
        return True
    if not isinstance(rules, list):
        return False
    for rule in rules:
        if not isinstance(rule, dict):
            return False
        path = str(rule.get("path") or "")
        op = str(rule.get("op") or "eq")
        expected = rule.get("value")
        actual = _resolve_path(payload, path) if path else payload
        if op == "exists":
            present = actual is not None
            if bool(expected) != present:
                return False
        elif actual is None:
            return False
        elif op == "eq":
            if actual != expected:
                return False
        elif op == "neq":
            if actual == expected:
                return False
        elif op == "in":
            if actual not in (expected if isinstance(expected, list) else [expected]):
                return False
        elif op == "not_in":
            if actual in (expected if isinstance(expected, list) else [expected]):
                return False
        elif op == "contains":
            if isinstance(actual, str):
                if str(expected) not in actual:
                    return False
            elif isinstance(actual, list):
                if expected not in actual:
                    return False
            else:
                return False
        elif op == "gt":
            try:
                if not (float(actual) > float(expected)):  # type: ignore[arg-type]
                    return False
            except (TypeError, ValueError):
                return False
        elif op == "lt":
            try:
                if not (float(actual) < float(expected)):  # type: ignore[arg-type]
                    return False
            except (TypeError, ValueError):
                return False
        else:
            return False  # unknown op → rule never matches (fail closed)
    return True


def match_filter_config(filter_config: dict[str, Any] | None, context: dict[str, Any]) -> bool:
    """Evaluate the full filter_config against a trigger context.

    Context keys: ``project_id``, ``labels`` (list[str]), ``priority``,
    ``actor_id``, ``title``, ``body``, ``payload`` (dict for payload_match).
    """
    config = filter_config or {}

    project_ids = _as_str_list(config.get("project_ids"))
    if project_ids and str(context.get("project_id") or "") not in project_ids:
        return False

    wanted_labels = _as_str_list(config.get("labels"))
    if wanted_labels:
        have = {str(item) for item in (context.get("labels") or [])}
        if not have.intersection(wanted_labels):
            return False

    priorities = _as_str_list(config.get("priorities"))
    if priorities and str(context.get("priority") or "") not in priorities:
        return False

    actor_ids = _as_str_list(config.get("actor_ids"))
    if actor_ids and str(context.get("actor_id") or "") not in actor_ids:
        return False

    title = str(context.get("title") or "")
    body = str(context.get("body") or "")
    haystack = f"{title}\n{body}"

    include = _as_str_list(config.get("keyword_include"))
    if include and not any(keyword in haystack for keyword in include):
        return False

    exclude = _as_str_list(config.get("keyword_exclude"))
    if exclude and any(keyword in haystack for keyword in exclude):
        return False

    if not match_payload_rules(config.get("payload_match"), context.get("payload") or {}):
        return False

    return True


def matched_dimensions(filter_config: dict[str, Any] | None) -> dict[str, Any]:
    """The non-empty filter dimensions — the dry-run echo (§3.2)."""
    config = filter_config or {}
    return {key: value for key, value in config.items() if value}


__all__ = ["PAYLOAD_OPS", "match_filter_config", "match_payload_rules", "matched_dimensions"]
