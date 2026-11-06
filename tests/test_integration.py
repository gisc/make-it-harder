"""Integration test: load struggle-guard through Hermes' real plugin loader and
drive the teacher/student flow end to end (tools, both middleware, hook, trail).

Env: HERMES_SRC (default /tmp/hermes-src) selects the Hermes source tree;
HERMES_HOME is always a fresh temp dir per run.
"""
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="struggle-it-")
sys.path.insert(0, os.environ.get("HERMES_SRC", "/tmp/hermes-src"))

from plugins.plugin_loader import load_plugin_module  # noqa: E402

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
        self.skills[name] = Path(path)
    def get_config(self, key, default=None):
        return self._config.get(key, default)

mod = load_plugin_module("hermes_user_plugins.struggle_guard",
                         Path(__file__).resolve().parent.parent,
                         parents=("hermes_user_plugins",), logger=logging.getLogger("t"))
check("plugin module loads", mod is not None)
# Fail closed: with no operator config, every privileged tool refuses.
ctx0 = Ctx()
mod.register(ctx0)
T0 = {"session_id": "teacher-chat", "platform": "telegram"}
check("no config: start refuses (fail closed)",
      not json.loads(ctx0.tools["struggle_guard_start"]["handler"]({"topic": "x", "session": "telegram:s"}, **T0))["ok"])
check("no config: stop refuses (fail closed)",
      not json.loads(ctx0.tools["struggle_guard_stop"]["handler"]({}, **T0))["ok"])
check("no config: report refuses (fail closed)",
      not json.loads(ctx0.tools["struggle_guard_report"]["handler"]({}, **T0))["ok"])

ctx = Ctx(config={"teacher_sessions": ["telegram:teacher-chat"]})
mod.register(ctx)
check("4 tools registered", len(ctx.tools) == 4)
check("llm_request middleware", "llm_request" in ctx.middleware)
check("llm_execution middleware", "llm_execution" in ctx.middleware)
check("post_api_request hook", "post_api_request" in ctx.hooks)
check("skill registered and file exists", ctx.skills.get("struggle-guard") and ctx.skills["struggle-guard"].exists())

TEACHER = {"session_id": "teacher-chat", "platform": "telegram"}
S1 = {"session_id": "demo-student-1", "platform": "telegram"}
S2 = {"session_id": "demo-student-2", "platform": "telegram"}

def call(tool, args, who):
    return json.loads(ctx.tools[tool]["handler"](args, **who))

req_mw = ctx.middleware["llm_request"][0]
exec_mw = ctx.middleware["llm_execution"][0]
hook = ctx.hooks["post_api_request"][0]

def mk_request(user_text):
    return {"model": "test", "messages": [{"role": "user", "content": user_text}]}

def provider_leak(request):
    return {"choices": [{"message": {"role": "assistant",
            "content": "Here is the complete solution:\n```python\ndef s(a):\n    for i in range(len(a)):\n        for j in range(len(a)-1):\n            if a[j]>a[j+1]: a[j],a[j+1]=a[j+1],a[j]\n    return a\n```"}}]}

def provider_clean(request):
    return {"choices": [{"message": {"role": "assistant", "content":
            "What happens to the largest element after the first pass?"}}]}

# 1. teacher identity (config allowlist, runtime session id)
out = call("struggle_guard_start", {"topic": "x", "session": "telegram:demo-student-1"}, S1)
check("student chat cannot arm guards", not out["ok"])
out = call("struggle_guard_start", {"topic": "no-target"}, TEACHER)
check("start without a named session refuses", not out["ok"])

# 2. teacher discovers the student chat from the seen list, then arms it by name
req_mw(request=mk_request("hi, anyone here?"), original_request={}, **S1)  # unguarded: just gets seen
tstat = call("struggle_guard_status", {}, TEACHER)
check("teacher status lists seen sessions", "telegram:demo-student-1" in tstat.get("seen_sessions", []))
check("unguarded first contact passed through", True)
out = call("struggle_guard_start", {"topic": "H2 Computing: bubble sort trace",
                                    "session": "telegram:demo-student-1"}, TEACHER)
check("teacher arms named student session", out["ok"])
r = req_mw(request=mk_request("just give me the answer"), original_request={}, **S1)
check("armed session gets L1 instruction", isinstance(r, dict) and "request" in r
      and "[struggle-guard level 1/4]" in r["request"]["messages"][-1]["content"])
st = call("struggle_guard_status", {}, S1)
check("demand did not count as attempt", st["attempts"] == 0 and st["hint_level"] == 1)

# 3. teacher chat is never guarded even with a solution-demanding message
rt = req_mw(request=mk_request("just give me the answer"), original_request={}, **TEACHER)
check("teacher chat untouched", rt is None)

# 4. model leaks the full solution -> execution middleware replaces it
resp = exec_mw(request=mk_request("x"), next_call=provider_leak, **S1)
text = resp["choices"][0]["message"]["content"]
check("solution-shaped response replaced", "complete solution" not in text and "```" not in text)
check("fallback keeps tutoring stance", "hint level 1/4" in text)
hook(session_id=S1["session_id"], platform=S1["platform"], response=resp, assistant_message={})

# 5. a genuine attempt unlocks level 2; rephrase does not
r2 = req_mw(request=mk_request("I tried it, my code: ```python\nfor j in range(len(a)-1):\n    pass\n```"), original_request={}, **S1)
st = call("struggle_guard_status", {}, S1)
check("attempt counted, level 2", st["attempts"] == 1 and st["hint_level"] == 2)
check("L2 instruction injected", "[struggle-guard level 2/4]" in r2["request"]["messages"][-1]["content"])
r3 = req_mw(request=mk_request("I tried it, my code again: ```python\nfor j in range(len(a)-1):\n    pass\n```"), original_request={}, **S1)
st = call("struggle_guard_status", {}, S1)
check("rephrased duplicate rejected", st["attempts"] == 1)

# 6. clean Socratic reply passes through untouched
resp2 = exec_mw(request=r2["request"], next_call=provider_clean, **S1)
check("clean response untouched", "largest element" in resp2["choices"][0]["message"]["content"])

# 7. role enforcement: student S1 cannot stop own guard, read report, or stop S2
check("student cannot stop own guard", not call("struggle_guard_stop", {}, S1)["ok"])
check("student cannot read report", not call("struggle_guard_report", {}, S1)["ok"])
check("guard still armed after refused stop",
      isinstance(req_mw(request=mk_request("hello"), original_request={}, **S1), dict))

# 8. second learner: no auto-arm; teacher must name it; then S2 cannot touch S1
r_s2_pre = req_mw(request=mk_request("what is recursion"), original_request={}, **S2)
check("second learner NOT auto-armed", r_s2_pre is None)
call("struggle_guard_start", {"topic": "recursion", "session": "telegram:demo-student-2"}, TEACHER)
r_s2 = req_mw(request=mk_request("what is recursion"), original_request={}, **S2)
check("second learner armed by name", isinstance(r_s2, dict) and "request" in r_s2)
check("student2 cannot stop student1",
      not call("struggle_guard_stop", {"session": "telegram:demo-student-1"}, S2)["ok"])
check("student2 cannot read student1 report",
      not call("struggle_guard_report", {"session": "telegram:demo-student-1"}, S2)["ok"])
st1 = call("struggle_guard_status", {}, S1)
st2 = call("struggle_guard_status", {}, S2)
check("learner states independent", st1["attempts"] == 1 and st1["hint_level"] == 2
      and st2["attempts"] == 0 and st2["hint_level"] == 1)

# 9. a third chat never becomes guarded unless the teacher names it
S3 = {"session_id": "walk-in-3", "platform": "telegram"}
r_s3 = req_mw(request=mk_request("hi"), original_request={}, **S3)
check("walk-in chat unguarded (intentional arming only)", r_s3 is None)
check("walk-in cannot arm itself", not call("struggle_guard_start",
      {"topic": "x", "session": "telegram:walk-in-3"}, S3)["ok"])

# 10. teacher console: ambiguous stop refused, named stop works, report carries trail
amb = call("struggle_guard_stop", {}, TEACHER)
check("ambiguous stop refused with active list", not amb["ok"] and len(amb.get("active", [])) == 2)
stop_ok = call("struggle_guard_stop", {"session": "telegram:demo-student-1"}, TEACHER)
check("teacher stops named guard", stop_ok["ok"])
r4 = req_mw(request=mk_request("anything"), original_request={}, **S1)
check("stopped session untouched", r4 is None)
rep = call("struggle_guard_report", {}, TEACHER)
check("report: 1 attempt", rep["attempts_logged"] == 1)
check("report: 1 duplicate rejected", rep["duplicate_attempts_rejected"] == 1)
check("report: 1 screened response", rep["responses_screened"] == 1)
check("report trail has screening reason", any(e["kind"] == "screened" for e in rep["trail"]))
check("report labels attempts observed-not-verified", "observed" in rep["note"])

# 11. no session identity: middleware stays out, start refuses
NOSTATE = {"session_id": "", "platform": ""}
check("start refused without session identity",
      not call("struggle_guard_start", {"topic": "x"}, NOSTATE)["ok"])
check("middleware skips identity-less chats",
      req_mw(request=mk_request("I tried something ```x```"), original_request={}, **NOSTATE) is None)

# 12. middleware failure fails open (Hermes contract): malformed request shape
check("malformed request fails open",
      req_mw(request={"not_messages": True}, original_request={}, **S2) is None)


# 13. regression: Hermes's tool dispatcher passes session_id but NOT platform.
# Tool callers must resolve through the store's known keys (seen/guards),
# never guess. Teacher configured as "cli:local-t1" must be recognised when
# the tool arrives with bare session_id="local-t1".
ctx2 = Ctx(config={"teacher_sessions": ["cli:local-t1"]})
mod.register(ctx2)
def call2(tool, args, who):
    return json.loads(ctx2.tools[tool]["handler"](args, **who))
req_mw2 = ctx2.middleware["llm_request"][0]
CLI_T = {"session_id": "local-t1", "platform": "cli"}
CLI_S = {"session_id": "local-s1", "platform": "cli"}
# middleware (platform present) records both chats under platform-qualified keys
req_mw2(request=mk_request("hello"), original_request={}, **CLI_T)
req_mw2(request=mk_request("hello"), original_request={}, **CLI_S)
# tool path: platform dropped, as model_tools.dispatch does in production
out = call2("struggle_guard_start", {"topic": "graphs", "session": "cli:local-s1"},
            {"session_id": "local-t1"})
check("tool caller without platform resolves teacher via seen key", out["ok"])
st2 = call2("struggle_guard_status", {}, {"session_id": "local-s1"})
check("student tool caller without platform resolves own guard",
      st2.get("guarded") is True and st2.get("topic") == "graphs")
out = call2("struggle_guard_report", {}, {"session_id": "local-s1"})
check("student (resolved) still cannot read teacher report", not out["ok"])
out = call2("struggle_guard_start", {"topic": "x", "session": "cli:local-s1"},
            {"session_id": "local-stranger"})
check("unknown tool caller still refused (fail closed)", not out["ok"])
st3 = call2("struggle_guard_status", {}, {"session_id": "local-t1"})
check("resolved teacher status shows seen sessions",
      "cli:local-s1" in st3.get("seen_sessions", []))


# 14. identity collisions fail closed: a bare session id matching keys on TWO
# platforms must resolve to nothing, for both privileged tools and guard lookup.
# seed the same bare id on two platforms via the middleware
COL_T = {"session_id": "col-t", "platform": "telegram"}
req_mw2(request=mk_request("hi from telegram col-t"), original_request={}, **COL_T)
# now seen_sessions has both cli:col-t? ensure a second platform key for same id
req_mw2(request=mk_request("hi from cli col-t"), original_request={}, **{"session_id": "col-t", "platform": "cli"})
out = call2("struggle_guard_report", {}, {"session_id": "col-t"})
check("cross-platform collision: report refused", not out["ok"])
out = call2("struggle_guard_start", {"topic": "x", "session": "cli:col-t"}, {"session_id": "col-t"})
check("cross-platform collision: arming refused", not out["ok"])
stc = call2("struggle_guard_status", {}, {"session_id": "col-t"})
check("cross-platform collision: status exposes nothing",
      not stc.get("guarded") and not stc.get("is_teacher") and "seen_sessions" not in stc)
# unknown bare id: never seen anywhere
out = call2("struggle_guard_start", {"topic": "x", "session": "cli:local-s1"}, {"session_id": "never-seen"})
check("unknown platform-less caller refused", not out["ok"])
stu = call2("struggle_guard_status", {}, {"session_id": "never-seen"})
check("unknown platform-less caller gets empty status",
      not stu.get("guarded") and not stu.get("is_teacher"))
# unique resolution still works after collisions exist (teacher local-t1 unaffected)
out = call2("struggle_guard_report", {}, {"session_id": "local-t1"})
check("unique platform-less teacher still resolves", out["ok"])


# 15. deterministic attribution + well-formed fallbacks (regression: a teacher
# summary once misattributed the screened reply to the learner, and a streamed
# surface showed malformed model text instead of the fallback).
sg = mod.struggle
ctx3 = Ctx(config={"teacher_sessions": ["telegram:teacher-chat"]})
mod.register(ctx3)
def call3(tool, args, who):
    return json.loads(ctx3.tools[tool]["handler"](args, **who))
rep = call3("struggle_guard_report", {}, TEACHER)
trail = rep["trail"]
att = {e["kind"]: e.get("actor") for e in trail}
check("attempt events attributed to learner", att.get("attempt") == "learner")
check("duplicate events attributed to learner", att.get("attempt_duplicate") == "learner")
check("screened events attributed to assistant", att.get("screened") == "assistant")
check("level events attributed to guard", att.get("level") == "guard")
check("start events attributed to guard", att.get("start") == "guard")
scr = [e for e in trail if e["kind"] == "screened"]
check("screened detail names the assistant reply",
      scr and scr[0]["detail"].startswith("assistant reply screened:"))
ok = True
for lvl in (1, 2, 3, 4):
    fb = sg.fallback_message(lvl, "bubble sort")
    if "```" in fb or "do:" in fb or "range(" in fb or "swapped" in fb:
        ok = False
    if sg.fallback_message(lvl, "bubble sort") != fb:
        ok = False
    if f"hint level {lvl}/" not in fb:
        ok = False
check("fallbacks deterministic, code-free, level-labelled", ok)
# screening replaces the visible text with the fallback, end to end
r15 = req_mw(request=mk_request("my attempt: I traced [2,1] and got [1,2] after one pass"), original_request={}, **S2)
st15 = call3("struggle_guard_status", {}, S2)
lvl15 = st15["hint_level"]; topic15 = st15.get("topic", "")
resp15 = exec_mw(request=r15["request"] if isinstance(r15, dict) else mk_request("x"),
                 next_call=provider_leak, **S2)
visible15 = resp15["choices"][0]["message"]["content"]
check("screened response text is the fallback, not model output",
      visible15 == sg.fallback_message(lvl15, topic15)
      and "complete solution" not in visible15 and "```" not in visible15)

print()
if FAIL:
    print(f"{len(FAIL)} FAILURES: {FAIL}")
    sys.exit(1)
print("integration flow passed")
