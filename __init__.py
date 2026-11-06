"""struggle-guard: Socratic tutor guardrail for Hermes.

The operator pins one or more teacher session identities in config
(plugins.entries.struggle-guard.settings.teacher_sessions). From a bound
teacher chat, struggle_guard_start arms a *named* student session
(discovered via struggle_guard_status's seen-sessions list). There is no
auto-enrollment: without configured teacher identities every privileged tool
fails closed, and no chat becomes guarded unless a teacher names it.

- llm_request middleware counts the student's observed attempts (deterministic
  state in plugin_db) and injects a hint-level instruction matching the level.
- llm_execution middleware screens the response for solution-shaped content
  beyond the current level and replaces it with a level-appropriate fallback.
- post_api_request logs the trail (attempts, level changes, screenings) that
  the teacher reads with struggle_guard_report.

Teacher identity: checked against the configured allowlist using the
runtime-provided session id, never user-supplied role or session text.

Honest limits, documented in README: Hermes middleware is fail-open, screening
is heuristic with measured misses, attempts are observed messages (not
verified learning), and next_call is single-use so we replace, not regenerate.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from . import struggle

logger = logging.getLogger(__name__)

_STORE: Optional[struggle.Store] = None
_CTX: Any = None
# session_key -> list of screening flags since the last API call, surfaced in the log.
_FLAGGED: dict[str, list[str]] = {}


def _store() -> struggle.Store:
    global _STORE
    if _STORE is None:
        from plugins.plugin_storage import plugin_db
        _STORE = struggle.Store(plugin_db("struggle-guard"))
    return _STORE


def _key_for(session_id: str = "", platform: str = "") -> str:
    return struggle.Store.key(session_id or "", platform or "")


def _caller_key(session_id: str = "", platform: str = "") -> str:
    """Identity of a tool caller. The middleware passes platform, but Hermes's
    tool dispatcher passes only session_id. An empty platform resolves only
    through the store's known keys, exactly and uniquely: an unknown or
    cross-platform-ambiguous session id resolves to "" and every privileged
    action fails closed. No identity is ever inferred."""
    sid = session_id or ""
    pf = platform or ""
    if not sid:
        return ""
    if pf:
        return _key_for(sid, pf)
    try:
        return _store().resolve_key(sid) or ""
    except Exception:
        return ""


def _guarded(session_id: str = "", platform: str = "") -> tuple[str, Optional[dict]]:
    """Resolve this chat's guard. No session identity -> ("", None): the
    middleware skips rather than risk keying two learners to one bucket."""
    if not session_id:
        return "", None
    try:
        key = _caller_key(session_id, platform)
        row = _store().guard(key)
        if row and row["active"]:
            return key, row
        return key, None
    except Exception as exc:  # never break a request on our own bookkeeping
        logger.warning("struggle-guard: guard lookup failed open: %s", exc)
        return _caller_key(session_id, platform), None


# --------------------------------------------------------------------- teacher identity

def _configured_teacher_sessions() -> list[str]:
    """Operator-pinned allowlist from config.yaml (trusted: operator-only write)."""
    if _CTX is None:
        return []
    try:
        raw = _CTX.get_config("teacher_sessions", [])
    except Exception as exc:
        logger.warning("struggle-guard: teacher_sessions config unreadable: %s", exc)
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _teacher_sessions() -> list[str]:
    # Fail closed: only the operator-configured allowlist counts. No enrollment.
    return _configured_teacher_sessions()


def _is_teacher(key: str) -> bool:
    return bool(key) and key in _teacher_sessions()


def _teacher_required(caller_key: str) -> Optional[str]:
    """Refusal text for teacher-console actions from non-teacher chats, else None."""
    if _is_teacher(caller_key):
        return None
    if not _teacher_sessions():
        return ("Teacher-console action refused: no teacher sessions are configured. The "
                "operator must set plugins.entries.struggle-guard.settings.teacher_sessions "
                "in config.yaml before struggle-guard can arm or manage guards.")
    return ("Teacher-console action refused: this chat is not a configured teacher session "
            "(plugins.entries.struggle-guard.settings.teacher_sessions).")


# --------------------------------------------------------------------- middleware

def on_llm_request(**kwargs: Any) -> Optional[dict]:
    """Inject the hint-level instruction for guarded sessions; count new attempts."""
    request = kwargs.get("request")
    if not isinstance(request, dict):
        return None
    key, row = _guarded(kwargs.get("session_id", ""), kwargs.get("platform", ""))
    if not key:
        return None
    try:
        _store().record_seen(key)  # teacher discovery: which chats exist
    except Exception:
        pass
    if _is_teacher(key):
        return None  # the teacher console is never guarded
    if not row:
        return None
    messages = request.get("messages")
    if not isinstance(messages, list):
        return None  # Responses-API mode etc.: no rewrite, execution screen still runs
    try:
        last_user = next(
            (str(m.get("content") or "") for m in reversed(messages)
             if isinstance(m, dict) and m.get("role") == "user"), "")
        if last_user:
            is_new, attempts = _store().record_attempt_if_new(key, last_user)
            if is_new:
                row["attempts"] = attempts
        level = struggle.level_for_attempts(row["attempts"])
        instruction = struggle.level_instruction(level, row["topic"])
        new_request = dict(request)
        new_request["messages"] = list(messages) + [{"role": "system", "content": instruction}]
        _store().log(key, "level", level, row["attempts"], "instruction injected")
        return {"request": new_request, "source": "struggle-guard",
                "reason": f"hint level {level} ({row['attempts']} attempts)"}
    except Exception as exc:
        logger.warning("struggle-guard: llm_request failed open: %s", exc)
        return None


def on_llm_execution(**kwargs: Any) -> Any:
    """Screen the response for guarded sessions; replace solution-shaped output."""
    request = kwargs.get("request")
    next_call = kwargs.get("next_call")
    key, row = _guarded(kwargs.get("session_id", ""), kwargs.get("platform", ""))
    if not key or _is_teacher(key) or not row:
        return next_call(request)
    response = next_call(request)  # single-use: we replace, never regenerate
    try:
        level = struggle.level_for_attempts(row["attempts"])
        text = struggle.extract_text(response)
        violation, reasons = struggle.screen_response(text, level)
        if violation:
            fallback = struggle.fallback_message(level, row["topic"])
            _FLAGGED.setdefault(key, []).extend(reasons)
            _store().log(key, "screened", level, row["attempts"],
                        "assistant reply screened: " + "; ".join(reasons))
            return struggle.replace_text(response, fallback)
        return response
    except Exception as exc:
        logger.warning("struggle-guard: execution screen failed open: %s", exc)
        return response


# --------------------------------------------------------------------- observer hook

def on_post_api_request(**kwargs: Any) -> None:
    """Append the guarded session's API call to the struggle trail. Never raises."""
    try:
        key, row = _guarded(kwargs.get("session_id", ""), kwargs.get("platform", ""))
        if not row:
            return
        level = struggle.level_for_attempts(row["attempts"])
        flagged = _FLAGGED.pop(key, [])
        detail = "screened: " + "; ".join(flagged) if flagged else "ok"
        _store().log(key, "api_call", level, row["attempts"], detail)
    except Exception as exc:
        logger.debug("struggle-guard: post_api_request log failed: %s", exc)


# --------------------------------------------------------------------- tools

def _ok(payload: dict) -> str:
    return json.dumps({"ok": True, **payload}, default=str)


def handle_start(args: dict, **kwargs: Any) -> str:
    """Teacher console: arm a guarded session for a NAMED student chat."""
    topic = str((args or {}).get("topic") or "").strip()
    target = str((args or {}).get("session") or "").strip()
    caller_key = _caller_key(kwargs.get("session_id", ""), kwargs.get("platform", ""))
    refusal = _teacher_required(caller_key)
    if refusal:
        return json.dumps({"ok": False, "error": refusal})
    if not target:
        return json.dumps({"ok": False, "error":
            "Which chat should be guarded? Pass session=<key> from the seen-sessions list "
            "in struggle_guard_status. No chat is guarded unless a teacher names it."})
    _store().start(target, topic)
    return _ok({"session": target, "topic": topic,
                "message": f"Guarded tutoring session armed for {target}."
                           + (f" Topic: {topic}." if topic else "")})


def handle_stop(args: dict, **kwargs: Any) -> str:
    requested = str((args or {}).get("session") or "").strip()
    caller_key = _caller_key(kwargs.get("session_id", ""), kwargs.get("platform", ""))
    refusal = _teacher_required(caller_key)
    if refusal:
        return json.dumps({"ok": False, "error": refusal})
    key = requested
    if not key:
        active = _store().list_active()
        if len(active) == 1:
            key = active[0]
        elif not active:
            return _ok({"message": "No active guarded sessions."})
        else:
            return json.dumps({"ok": False, "error":
                "Multiple active guards; pass the session key to stop.", "active": active})
    row = _store().guard(key)
    if not row or not row["active"]:
        return json.dumps({"ok": False, "error": f"No active guard for session {key!r}."})
    _store().stop(key)
    return _ok({"session": key, "message": "Guarded tutoring session stopped."})


def handle_status(args: dict, **kwargs: Any) -> str:
    key, row = _guarded(kwargs.get("session_id", ""), kwargs.get("platform", ""))
    payload: dict[str, Any] = {"session": key, "is_teacher": _is_teacher(key)}
    if _is_teacher(key):
        payload["active_guards"] = _store().list_active()
        payload["seen_sessions"] = _store().list_seen()
    if not row:
        payload["guarded"] = False
        return _ok(payload)
    payload.update({"guarded": True, "topic": row["topic"], "attempts": row["attempts"],
                    "hint_level": struggle.level_for_attempts(row["attempts"]),
                    "max_level": struggle.MAX_LEVEL,
                    "note": "attempts are observed messages, not verified learning"})
    return _ok(payload)


def handle_report(args: dict, **kwargs: Any) -> str:
    caller_key = _caller_key(kwargs.get("session_id", ""), kwargs.get("platform", ""))
    refusal = _teacher_required(caller_key)
    if refusal:
        return json.dumps({"ok": False, "error": refusal})
    requested = str((args or {}).get("session") or "").strip()
    events = _store().report(requested or None)
    screened = [e for e in events if e["kind"] == "screened"]
    attempts = [e for e in events if e["kind"] == "attempt"]
    duplicates = [e for e in events if e["kind"] == "attempt_duplicate"]
    return _ok({"session": requested or "all", "events": len(events),
                "attempts_logged": len(attempts), "duplicate_attempts_rejected": len(duplicates),
                "responses_screened": len(screened),
                "note": "attempts are observed messages, not verified learning",
                "trail": events[-50:]})


_SCHEMAS = {
    "struggle_guard_start": {
        "name": "struggle_guard_start",
        "description": ("Teacher console: arm a guarded tutoring session for a named student "
                        "chat. Only runs from a configured teacher session; the target session "
                        "key comes from struggle_guard_status's seen-sessions list."),
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "topic": {"type": "string", "description": "What the student is working on, e.g. 'H2 Computing 9569: bubble sort trace'."},
            "session": {"type": "string", "description": "Session key of the student chat to guard (from the seen-sessions list)."}}}},
    "struggle_guard_stop": {
        "name": "struggle_guard_stop",
        "description": ("Teacher console: stop a guarded tutoring session. Pass the session "
                        "key when more than one guard is active."),
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "session": {"type": "string", "description": "Session key to stop; default: the only active guard."}}}},
    "struggle_guard_status": {
        "name": "struggle_guard_status",
        "description": "Show this chat's guard state: attempts observed, current hint level, open seats.",
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}}},
    "struggle_guard_report": {
        "name": "struggle_guard_report",
        "description": ("Teacher console: the learning trail - observed attempts, hint level "
                        "changes, rejected duplicate attempts, and any responses the guard screened."),
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "session": {"type": "string", "description": "Session key to report on; default: all sessions."}}}},
}

_HANDLERS = {
    "struggle_guard_start": handle_start,
    "struggle_guard_stop": handle_stop,
    "struggle_guard_status": handle_status,
    "struggle_guard_report": handle_report,
}


def register(ctx) -> None:
    global _CTX
    _CTX = ctx
    for name, handler in _HANDLERS.items():
        ctx.register_tool(name=name, toolset="struggle-guard", schema=_SCHEMAS[name], handler=handler)
    ctx.register_middleware("llm_request", on_llm_request)
    ctx.register_middleware("llm_execution", on_llm_execution)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_skill("struggle-guard", Path(__file__).parent / "skills" / "struggle-guard" / "SKILL.md")
