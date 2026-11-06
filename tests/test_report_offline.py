"""Offline regression for the teacher report path (no Hermes, no model).

2026-11-06 Olares root run: the live teacher report could not be exercised
(CLI timeout, then the harness session store lost the teacher session). That
failure was session-lifecycle, not report logic: handle_report reads only the
plugin db. This suite proves the report logic and its deterministic actor
attribution offline against a synthetic store.

Run: python3 tests/test_report_offline.py
"""
import importlib.util
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import struggle  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "sg_offline", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT])
mod = importlib.util.module_from_spec(spec)
sys.modules["sg_offline"] = mod
spec.loader.exec_module(mod)

FAIL = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAIL.append(name)

class Ctx:
    def __init__(self, config=None):
        self.tools, self.middleware, self.hooks, self.skills = {}, {}, {}, {}
        self._config = config or {}
    def register_tool(self, name, toolset, schema, handler):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}
    def register_middleware(self, kind, fn):
        self.middleware.setdefault(kind, []).append(fn)
    def register_hook(self, event, fn):
        self.hooks.setdefault(event, []).append(fn)
    def register_skill(self, name, path):
        self.skills[name] = path
    def get_config(self, key, default=None):
        return self._config.get(key, default)

# Synthetic store + registered plugin, teacher allowlist pinned by the operator.
mod._STORE = struggle.Store(sqlite3.connect(":memory:"))
ctx = Ctx(config={"teacher_sessions": ["cli:teacher-1"]})
mod.register(ctx)
T = {"session_id": "teacher-1", "platform": "cli"}
STUDENT = "cli:student-1"

r = json.loads(ctx.tools["struggle_guard_start"]["handler"](
    {"topic": "Python string reversal", "session": STUDENT}, **T))
check("teacher arms guard", r["ok"] and r["session"] == STUDENT)

# Learner trail: one proposal attempt, one exact-resend duplicate, one screened reply.
mod._STORE.record_attempt_if_new(
    STUDENT, "I think it should loop from the end backwards, one character at a time.")
mod._STORE.record_attempt_if_new(
    STUDENT, "I think it should loop from the end backwards, one character at a time.")
mod._STORE.log(STUDENT, "screened", 1, 1, "assistant reply screened: fenced code 6 lines > level 1 allowance 0")

rep = json.loads(ctx.tools["struggle_guard_report"]["handler"]({"session": STUDENT}, **T))
check("report ok", rep["ok"])
check("report counts attempts", rep["attempts_logged"] == 1, str(rep.get("attempts_logged")))
check("report counts rejected duplicates", rep["duplicate_attempts_rejected"] == 1)
check("report counts screened responses", rep["responses_screened"] == 1)
trail = rep["trail"]
check("trail includes the arm, the attempt, the duplicate, the screening",
      {"start", "attempt", "attempt_duplicate", "screened"} <= {e["kind"] for e in trail})
actors = {e["kind"]: e.get("actor") for e in trail}
check("actor attribution: attempts are the learner",
      actors.get("attempt") == "learner" and actors.get("attempt_duplicate") == "learner")
check("actor attribution: screening is the assistant", actors.get("screened") == "assistant")
check("actor attribution: arming is the guard", actors.get("start") == "guard")
check("report is scoped to the requested session",
      all(e["session_key"] == STUDENT for e in trail))

# Fail closed: non-teacher and unknown callers get refused, never the trail.
r2 = json.loads(ctx.tools["struggle_guard_report"]["handler"]({}, session_id="student-1", platform="cli"))
check("student cannot read the report (fail closed)", not r2["ok"])
r3 = json.loads(ctx.tools["struggle_guard_report"]["handler"]({}, session_id="ghost", platform=""))
check("unknown platform-less caller cannot read the report (fail closed)", not r3["ok"])

# Platform-less teacher (Hermes tool dispatcher drops platform): resolves only
# because the middleware recorded the teacher chat as seen.
mod._STORE.record_seen("cli:teacher-1")
r4 = json.loads(ctx.tools["struggle_guard_report"]["handler"]({"session": STUDENT}, session_id="teacher-1", platform=""))
check("platform-less teacher resolves via seen_sessions", r4["ok"])

print()
print("offline report suite:", "FAILED" if FAIL else "all passed")
sys.exit(1 if FAIL else 0)
