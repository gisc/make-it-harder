"""Core logic for struggle-guard. Pure functions plus a small sqlite store,
kept separate from Hermes glue so the whole file is unit-testable without Hermes.

Design notes (see README):
- The hint level is deterministic state: level = min(1 + attempts, MAX_LEVEL).
  A prompt can ask the model to behave; only code can hold the level.
- Response screening is heuristic. It catches solution-shaped output in the
  forms the adversarial suite measures, and misses some others; the suite
  prints every observed miss as a miss. Hermes middleware also fails open on
  plugin errors, so a plugin crash removes the screen entirely.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Optional

MAX_LEVEL = 4

# ---------------------------------------------------------------- attempt detection

# name, regex; "try-family" names are negation-guarded below ("I haven't tried
# anything" is not an attempt).
_ATTEMPT_PATTERNS = [
    ("code", re.compile(r"```", re.IGNORECASE | re.DOTALL)),
    ("error", re.compile(r"\btraceback\b|\berror\b|\bexception\b|\bfailed\b", re.IGNORECASE)),
    ("try-own-work", re.compile(
        r"\bi(?:'ve|\s+\w+)?\s+tried\b|\bi wrote\b|\bmy answer\b|\bmy solution\b",
        re.IGNORECASE)),
    ("try-work-verb", re.compile(
        r"\bi\s+(?:ran|tested|checked|traced|watched|stepped|debugged|tried)\b", re.IGNORECASE)),
    ("edit-verb", re.compile(
        r"\bi\s+(?:changed|add(?:ed)?|fixed|updated|rewrote|modified|adjusted|simplified)\b",
        re.IGNORECASE)),
    ("test-claim", re.compile(
        r"\btested\s+on\b|\bit\s+(?:works|worked|sorts?)\s+(?:correctly|fine|right|now)\b"
        r"|\b(?:both|all)\s+(?:of\s+them\s+)?(?:sort|pass|work)\b", re.IGNORECASE)),
    ("output-claim", re.compile(
        r"\bi\s+(?:got|get)\b|\bit\s+(?:gives|gave|outputs?|prints?|returns?)\b"
        r"|\bgot\s*[\[\(\"'-]|\bgot\s+-?\d", re.IGNORECASE)),
    ("not-working", re.compile(r"\bdoesn'?t work\b|\bnot working\b|\bstuck\b", re.IGNORECASE)),
    ("result-question", re.compile(
        r"\bis it\s+\S{1,20}\?|\banswer\s*=\s*\S+|\b=\s*-?\d[\d.,]*\s*\??\s*$", re.IGNORECASE)),
    ("my-approach", re.compile(r"\bmy (approach|idea|attempt|working|workings|loop|pass)\b", re.IGNORECASE)),
    ("here-what-i-did", re.compile(r"\bhere('s| is) what i (did|have|tried)\b", re.IGNORECASE)),
    ("own-work-question", re.compile(
        r"\bwhat am i (?:doing|missing)\b|\bwhy (?:isn'?t|doesn'?t|won'?t) (?:it|my|this)\b", re.IGNORECASE)),
    # First-person approach proposals: the student reasons about a method in
    # words without claiming finished work ("I think it should loop backwards",
    # "what if I slice it"). Mere answer-demands ("give me the code", "can I
    # have the solution") carry no proposal marker and stay non-attempts.
    ("approach-proposal", re.compile(
        r"\bi think (?:it|i|we) (?:should|could|need to|might|would)\b"
        r"|\bi'?m thinking (?:of|about)\b"
        r"|\bwhat if (?:i|we)\b"
        r"|\bwould it work if\b"
        r"|\bmy plan is\b"
        r"|\bis (?:that|this) approach (?:right|ok|okay|correct|good|wrong|sensible)\b",
        re.IGNORECASE)),
]
_TRY_FAMILY = {"try-own-work", "try-work-verb", "edit-verb", "test-claim"}
_NEGATED_TRY = re.compile(
    r"\b(?:haven'?t|hasn'?t|have not|has not|didn'?t|did not|don'?t|do not|never)\b"
    r"[^.!?]{0,30}\b(?:try|tried|attempt|attempted|start|started|do|done|write|written|"
    r"run|ran|test|tested|change|changed|add|added|fix|fixed|update|updated|rewrite|"
    r"rewrote|modify|modified|adjust|adjusted|simplify|simplified)\b", re.IGNORECASE)

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")

def attempt_fingerprint(text: str) -> frozenset:
    """Normalized content tokens, for near-duplicate attempt detection."""
    return frozenset(_TOKEN_RE.findall(text.lower()))

def detect_attempt(text: str) -> tuple[bool, list[str]]:
    """Does this student message carry evidence of their own attempt?

    Requires both a recognised attempt signal and minimal substance (2+
    content tokens, or pasted code), so a bare 'error' or 'stuck' does not
    farm the hint ladder.
    """
    if not text or len(text.strip()) < 3:
        return False, []
    hits = [name for name, p in _ATTEMPT_PATTERNS if p.search(text)]
    if hits and _NEGATED_TRY.search(text):
        hits = [h for h in hits if h not in _TRY_FAMILY]
    if not hits:
        return False, []
    if "```" not in text and len(attempt_fingerprint(text)) < 2:
        return False, []
    return True, hits

def fingerprints_similar(a: frozenset, b: frozenset, threshold: float = 0.7) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= threshold

# Concrete anchors: bracket/paren code tokens and numbers. A reworded repeat of the
# SAME work keeps the same anchors; genuinely new work usually adds one.
_ANCHOR_RE = re.compile(r"\w+\[[^\]\n]{1,20}\]|\w+\([^()\n]{0,20}\)|\d+")

def attempt_anchors(text: str) -> frozenset:
    return frozenset(_ANCHOR_RE.findall(text.lower()))

def attempts_same_work(new_text: str, new_fp: frozenset, old_text: str) -> bool:
    """True when new_text is the same work restated: similar overall wording
    (Jaccard >= 0.4), old anchors fully contained, and no novel concrete anchor.
    A delta of pure numbers alone does not make restated work new: rewording the
    same proposal with a tweaked value ("use 2 loops" -> "use 3 loops") is the
    same work when the wording stays this similar (anti-farming)."""
    old_fp = attempt_fingerprint(old_text)
    if not new_fp or not old_fp:
        return False
    j = len(new_fp & old_fp) / len(new_fp | old_fp)
    if j < 0.4:
        return False
    new_a, old_a = attempt_anchors(new_text), attempt_anchors(old_text)
    if not old_a:
        return j >= 0.7
    digits = {a for a in new_a | old_a if a.isdigit()}
    if not (new_a - old_a) - digits and not (old_a - new_a) - digits:
        return j >= 0.5
    if new_a - old_a:
        return False
    return len(new_a & old_a) / len(old_a) >= 0.8

def level_for_attempts(attempts: int, max_level: int = MAX_LEVEL) -> int:
    return min(1 + max(0, attempts), max_level)

# ---------------------------------------------------------------- level instructions

# What each level permits the tutor to do. Level 4 is the top of the ladder:
# a small worked example on DIFFERENT data, never the student's own problem.
_LEVEL_BRIEFS = {
    1: ("Ask ONE short Socratic question (under 25 words) about the very next decision in the "
        "student's own approach - a question only. Ground it in what they actually said. Never "
        "stack two questions, never answer it yourself. No hints, no partial answers, no code, "
        "no pseudocode."),
    2: ("Give ONE conceptual hint: name the idea or pattern they need, not how to apply it. "
        "No code, no pseudocode, no worked arithmetic."),
    3: ("Give a structural hint: outline the approach in at most 3 short bullets or a tiny "
        "pseudocode fragment of at most 3 lines. Never a complete solution, never full code."),
    4: ("Use a small analogous worked example on DIFFERENT numbers/data than the student's "
        "problem. Illustrative snippets of at most 8 lines. Never solve their exact problem."),
}

def level_instruction(level: int, topic: str = "") -> str:
    brief = _LEVEL_BRIEFS.get(level, _LEVEL_BRIEFS[1])
    topic_line = f" The student is working on: {topic}." if topic else ""
    return (
        f"[struggle-guard level {level}/{MAX_LEVEL}] You are tutoring a student who must "
        f"learn by doing.{topic_line} {brief} When the student shows work, first acknowledge "
        "their specific progress in a few words. If they ask for the answer directly, encourage "
        "their attempt instead. Never provide a complete solution to their exact problem."
    )

# ---------------------------------------------------------------- response screening

_SOLUTION_PHRASES = [
    r"\bthe answer is\b", r"\bthe solution is\b", r"\bfinal answer\b",
    r"\bhere (is|'s) the (complete|full|entire)\b", r"\b(full|complete) solution\b",
    r"\bcopy (this|the code below)\b", r"\bstep[- ]by[- ]step solution\b",
    r"\bhere (is|'s) (the|your) (code|program|script|function)\b",
]
_SOLUTION_RE = [re.compile(p, re.IGNORECASE) for p in _SOLUTION_PHRASES]
_CODE_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)
_STEP_SEQ_RE = re.compile(r"\bstep\s*1\b.*\bstep\s*2\b.*(\bstep\s*3\b|\bfinal\b)", re.IGNORECASE | re.DOTALL)

# Unfenced code: lines that carry code syntax even without a fence.
_CODE_LINE_RES = [
    re.compile(p) for p in [
        r"^\s*(?:for|while|if|elif|else|def|class|return|print|import|from|raise|with|try|except)\b.*[():]",
        r"\b(?:if|for|while)\s+[^\n]{0,40}\[[^\]]+\][^\n]{0,40}:",
        r"\b(?:for|while)\s+\w+\s+in\s+.+:",
        r"\w[\w\.\[\]'\"]*\s*=[^=]",
        r"\w+\s*(?:==|!=|\+=|-=|<=|>=)\s*\S",
        r"\b(?:def|return|print)\s+\w.*[()]",
    ]
]
_INLINE_CODE_RE = re.compile(r"`([^`\n]{6,})`")

# Prose recipes: an answer written as imperative algorithm steps instead of code.
_PROCEDURE_VERB_RE = re.compile(
    r"\b(compare|swap|repeat|loop|iterate|return|print|check|append|remove|delete|"
    r"sort|increment|decrement|assign|call|compute|calculate|multiply|divide|"
    r"traverse|recurse|split|merge|push|pop|enqueue|dequeue|initiali[sz]e)\b",
    re.IGNORECASE)
_RECIPE_WRAPUP_RE = re.compile(
    r"\b(that'?s (all|it|the whole)|and you'?re done|that is all there is|in summary, the algorithm)\b",
    re.IGNORECASE)

def _fenced_code_lines(text: str) -> int:
    total = 0
    for m in _CODE_FENCE_RE.finditer(text):
        total += sum(1 for ln in m.group(1).splitlines() if ln.strip())
    return total

def _unfenced_code_lines(text: str) -> int:
    without_fences = _CODE_FENCE_RE.sub("\n", text)
    total = 0
    for line in without_fences.splitlines():
        line = line.strip()
        if len(line) < 4:
            continue
        if any(rx.search(line) for rx in _CODE_LINE_RES):
            total += 1
    return total

def _inline_code_spans(text: str) -> int:
    """Backtick spans carrying real code. Short references like `len(a)` are
    allowed (a tutor may name a function); assignments, multi-statements and
    long call chains are not."""
    n = 0
    for m in _INLINE_CODE_RE.finditer(text):
        body = m.group(1)
        if any(t in body for t in ("=", ";", ":")) or ("(" in body and ")" in body and len(body) >= 12):
            n += 1
    return n

def _procedure_verb_count(text: str) -> int:
    return len({m.group(1).lower() for m in _PROCEDURE_VERB_RE.finditer(text)})

def screen_response(text: str, level: int) -> tuple[bool, list[str]]:
    """Is this response solution-shaped beyond what the hint level permits?

    Heuristic layers, each measured in tests/test_adversarial.py:
    solution phrases, fenced code over allowance, unfenced code lines,
    inline code spans, step-by-step worked solutions, prose recipes (L1-L2).
    """
    if not text:
        return False, []
    reasons: list[str] = []
    allowance = {1: 0, 2: 0, 3: 3, 4: 8}.get(level, 0)

    for rx in _SOLUTION_RE:
        if rx.search(text):
            reasons.append(f"solution phrase: /{rx.pattern}/")

    fenced = _fenced_code_lines(text)
    if fenced > allowance:
        reasons.append(f"fenced code {fenced} lines > level {level} allowance {allowance}")

    unfenced = _unfenced_code_lines(text)
    if unfenced > allowance:
        reasons.append(f"unfenced code {unfenced} lines > level {level} allowance {allowance}")

    if level <= 2:
        spans = _inline_code_spans(text)
        if spans:
            reasons.append(f"{spans} inline code span(s) at level {level}")
        verbs = _procedure_verb_count(text)
        if verbs >= 3 or (verbs >= 2 and _RECIPE_WRAPUP_RE.search(text)):
            reasons.append(f"prose recipe: {verbs} distinct procedure verbs at level {level}")
        if _STEP_SEQ_RE.search(text) and (fenced or unfenced):
            reasons.append("step-by-step worked solution with code")
    return bool(reasons), reasons

def fallback_message(level: int, topic: str = "") -> str:
    topic_bit = f" on {topic}" if topic else ""
    what = topic if topic else "this problem"
    prompts = {
        1: (f"You can do this one step at a time. What is the very first decision a solution "
            f"to {what} has to make? Tell me your thinking in words."),
        2: (f"Which idea is {what} really testing? Name the concept out loud - then ask "
            f"yourself where your version first needs it."),
        3: (f"The shape of {what}, not the answer: what goes in, what repeats, when it stops, "
            f"what comes out. Which piece do you want to think through first?"),
        4: (f"Walk me through {what} on a tiny made-up input by hand, in words - what happens "
            f"at each step?"),
    }
    return (
        "I'm here to help you learn this, so I won't hand over the finished answer - "
        + prompts.get(level, prompts[1])
        + f"\n\n(Working{topic_bit} - hint level {level}/{MAX_LEVEL}.)"
    )

# ---------------------------------------------------------------- response plumbing

def extract_text(response: Any) -> str:
    """Best-effort text extraction across provider response shapes."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        try:
            return str(response["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            pass
        for key in ("content", "text", "output_text"):
            if isinstance(response.get(key), str):
                return response[key]
        return ""
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    choices = getattr(response, "choices", None)
    if choices:
        try:
            return str(choices[0].message.content or "")
        except (AttributeError, IndexError, TypeError):
            return ""
    return ""

def replace_text(response: Any, new_text: str) -> Any:
    """Return the response with its visible text replaced, preserving the shape."""
    if isinstance(response, str):
        return new_text
    if isinstance(response, dict):
        try:
            response["choices"][0]["message"]["content"] = new_text
            return response
        except (KeyError, IndexError, TypeError):
            pass
        for key in ("content", "text", "output_text"):
            if isinstance(response.get(key), str):
                response[key] = new_text
                return response
        response["content"] = new_text
        return response
    if getattr(response, "content", None) is not None:
        try:
            response.content = new_text
            return response
        except AttributeError:
            pass
    choices = getattr(response, "choices", None)
    if choices:
        try:
            choices[0].message.content = new_text
        except (AttributeError, IndexError, TypeError):
            pass
    return response

# ---------------------------------------------------------------- persistent state

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guards (
  session_key TEXT PRIMARY KEY,
  topic TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_attempt_hash TEXT NOT NULL DEFAULT '',
  attempt_fps TEXT NOT NULL DEFAULT '[]',
  started_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS seen_sessions (
  session_key TEXT PRIMARY KEY,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS struggle_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  session_key TEXT NOT NULL,
  kind TEXT NOT NULL,
  level INTEGER NOT NULL,
  attempts INTEGER NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
"""

_MAX_FPS = 10

class Store:
    """Thin sqlite store. The connection comes from plugins.plugin_storage.plugin_db
    in production and from plain sqlite3 in tests."""

    def __init__(self, conn):
        self.conn = conn
        self.conn.executescript(_SCHEMA)
        try:  # upgrade databases created before attempt_fps existed
            self.conn.execute("ALTER TABLE guards ADD COLUMN attempt_fps TEXT NOT NULL DEFAULT '[]'")
        except Exception:
            pass
        self.conn.commit()

    @staticmethod
    def key(session_id: str = "", platform: str = "") -> str:
        """State key for a chat. Empty session ids never share a bucket: they
        return '' and callers refuse/skip rather than conflate learners."""
        if not session_id:
            return ""
        return f"{platform}:{session_id}" if platform else session_id

    def start(self, key: str, topic: str) -> None:
        self.conn.execute(
            "INSERT INTO guards(session_key, topic, active, attempts, started_at) VALUES(?,?,1,0,?) "
            "ON CONFLICT(session_key) DO UPDATE SET topic=excluded.topic, active=1, attempts=0, "
            "last_attempt_hash='', attempt_fps='[]', started_at=excluded.started_at",
            (key, topic, time.time()))
        self.conn.commit()
        self.log(key, "start", 1, 0, topic)

    def stop(self, key: str) -> None:
        self.conn.execute("UPDATE guards SET active=0 WHERE session_key=?", (key,))
        self.conn.commit()
        row = self.guard(key)
        if row:
            self.log(key, "stop", level_for_attempts(row["attempts"]), row["attempts"], "")

    def resolve_key(self, session_id: str) -> Optional[str]:
        """Resolve a platform-less session id (Hermes's tool dispatcher drops
        platform) to a stored key, or None when it is unknown or ambiguous.
        An exact bare-key match counts; otherwise exactly one stored key with
        ':<id>' as suffix must exist across guards and seen_sessions. Two or
        more distinct matches (a cross-platform collision) return None:
        callers fail closed rather than guess an identity."""
        if not session_id:
            return None
        suffix = ":" + session_id
        matches = set()
        for table in ("guards", "seen_sessions"):
            try:
                rows = self.conn.execute(
                    f"SELECT session_key FROM {table} WHERE session_key = ? "
                    "OR substr(session_key, -?) = ?",
                    (session_id, len(suffix), suffix)).fetchall()
            except Exception:
                continue
            matches.update(r[0] for r in rows)
        if len(matches) == 1:
            return matches.pop()
        return None

    def guard(self, key: str) -> Optional[dict]:
        cur = self.conn.execute(
            "SELECT session_key, topic, active, attempts, last_attempt_hash, attempt_fps, started_at "
            "FROM guards WHERE session_key=?", (key,))
        row = cur.fetchone()
        if not row:
            return None
        cols = ["session_key", "topic", "active", "attempts", "last_attempt_hash", "attempt_fps", "started_at"]
        return dict(zip(cols, row))

    def _recent_attempt_texts(self, key: str, limit: int = 10) -> list[str]:
        cur = self.conn.execute(
            "SELECT detail FROM struggle_log WHERE session_key=? AND kind='attempt' "
            "ORDER BY id DESC LIMIT ?", (key, limit))
        return [r[0] for r in cur.fetchall() if r and r[0]]

    def record_attempt_if_new(self, key: str, message_text: str) -> tuple[bool, int]:
        """Bump attempts once per distinct attempt-carrying message.

        Resists farming four ways: exact-message dedupe, near-duplicate
        fingerprints (Jaccard >= 0.7 against recent attempts), the same-work
        anchor rule (including number-only deltas), and the substance floor
        in detect_attempt. Returns (is_new, attempts).
        """
        row = self.guard(key)
        if not row or not row["active"]:
            return False, 0
        digest = hashlib.sha256(message_text.strip().encode("utf-8", "replace")).hexdigest()[:16]
        if digest == row["last_attempt_hash"]:
            self.log(key, "attempt_duplicate", level_for_attempts(row["attempts"]),
                     row["attempts"], message_text[:200])
            return False, row["attempts"]
        is_attempt, _signals = detect_attempt(message_text)
        if not is_attempt:
            return False, row["attempts"]
        fp = attempt_fingerprint(message_text)
        try:
            past = [frozenset(x) for x in json.loads(row.get("attempt_fps") or "[]")]
        except (ValueError, TypeError):
            past = []
        past_texts = self._recent_attempt_texts(key)
        same_work = any(attempts_same_work(message_text, fp, t) for t in past_texts)
        if same_work or any(fingerprints_similar(fp, old) for old in past):
            self.log(key, "attempt_duplicate", level_for_attempts(row["attempts"]),
                     row["attempts"], message_text[:200])
            return False, row["attempts"]
        attempts = row["attempts"] + 1
        past = past[-(_MAX_FPS - 1):] + [fp]
        self.conn.execute(
            "UPDATE guards SET attempts=?, last_attempt_hash=?, attempt_fps=? WHERE session_key=?",
            (attempts, digest, json.dumps([sorted(x) for x in past]), key))
        self.conn.commit()
        self.log(key, "attempt", level_for_attempts(attempts), attempts, message_text[:500])
        return True, attempts

    def log(self, key: str, kind: str, level: int, attempts: int, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO struggle_log(ts, session_key, kind, level, attempts, detail) VALUES(?,?,?,?,?,?)",
            (time.time(), key, kind, level, attempts, detail[:500]))
        self.conn.commit()

    def meta_get(self, key: str, default: str = "") -> str:
        cur = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row else default

    def meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value))
        self.conn.commit()

    def record_seen(self, key: str) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO seen_sessions(session_key, first_seen, last_seen) VALUES(?,?,?) "
            "ON CONFLICT(session_key) DO UPDATE SET last_seen=excluded.last_seen",
            (key, now, now))
        self.conn.commit()

    def list_seen(self) -> list[str]:
        cur = self.conn.execute("SELECT session_key FROM seen_sessions ORDER BY last_seen DESC")
        return [r[0] for r in cur.fetchall()]

    def list_active(self) -> list[str]:
        cur = self.conn.execute("SELECT session_key FROM guards WHERE active=1 ORDER BY started_at")
        return [r[0] for r in cur.fetchall()]

    # Who produced each trail event, derived deterministically from its kind:
    # attempts come from learner messages, screening from the assistant's own
    # reply, and level/start/stop from the guard itself. Report readers must
    # never have to infer attribution from free text.
    ACTOR_BY_KIND = {
        "attempt": "learner",
        "attempt_duplicate": "learner",
        "screened": "assistant",
        "api_call": "assistant",
        "level": "guard",
        "start": "guard",
        "stop": "guard",
    }

    def report(self, key: Optional[str] = None) -> list[dict]:
        q = ("SELECT session_key, kind, level, attempts, detail, ts FROM struggle_log "
             + ("WHERE session_key=? " if key else "") + "ORDER BY ts")
        cur = self.conn.execute(q, (key,) if key else ())
        events = [dict(zip(["session_key", "kind", "level", "attempts", "detail", "ts"], r))
                  for r in cur.fetchall()]
        for e in events:
            e["actor"] = self.ACTOR_BY_KIND.get(e["kind"], "guard")
        return events
