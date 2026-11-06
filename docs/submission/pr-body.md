## What it is

**Make It Harder** (working slug `struggle-guard`; tagline: "Life is hard. Why not make it harder.") turns a Hermes chat into a guarded Socratic tutoring session.
A teacher arms a named student chat; from then on:

- **Hint ladder in code, not in the prompt.** Observed student attempts
  (code, errors with context, working, proposed answers) are detected in
  `llm_request` middleware and stored in SQLite (`plugin_db`). Hint level =
  `min(1 + attempts, 4)`: Socratic question -> concept -> structure ->
  worked example on different data. The exact solution to the student's own
  problem is never the intended output at any level - that is the screening
  policy; its measured misses are published below.
- **Anti-farming.** Substance floor, exact-resend dedupe, and same-work
  restatement detection (wording overlap Jaccard >= 0.7, or shared concrete
  anchors - code tokens and test data - with Jaccard >= 0.4 and no new
  anchor). This catches the measured reworded-repeat cases; broader
  fabrication stays possible (see honest limits). Rejected duplicates are
  logged for the teacher.
- **Response screening.** `llm_execution` middleware screens replies for
  solution-shaped content beyond the current level (solution phrases,
  fenced/unfenced code over allowance, inline code spans, step-by-step
  solutions, prose recipes) and replaces violations with a level-appropriate
  fallback (next_call is single-use, so replace-not-regenerate).
- **Teacher console.** Teacher identity = operator-configured session-id
  allowlist (`plugins.entries.struggle-guard.settings.teacher_sessions`);
  without it every privileged tool fails closed. Students cannot arm, stop,
  or read reports - including via prompt injection through the model. Guards
  key on `platform:session_id`; identity-less chats are refused.
- **Learning trail.** Attempts, rejected duplicates, level changes, API calls
  and screenings in a local `struggle_log` table; `struggle_guard_report`
  summarises for the teacher.

## Honest limits (also in README)

- Screening is heuristic with **measured, published misses**: the adversarial
  suite prints every known miss as KNOWN MISS (currently: single-verb
  paraphrases; answers split across replies). The suite fails if a known miss
  starts being caught without being promoted to the must-catch set.
- Hermes middleware is fail-open: a plugin error removes the screen.
- Attempts are *observed messages, not verified learning*; a student who
  fabricates plausible attempts, or rewords earlier work with fresh concrete
  details, can climb - the trail exists so the teacher can judge.
- Multi-turn screening results (6/6 piecemeal leaks caught) are measured
  offline against injected leaking responses, not yet against a live model;
  live leakage behaviour depends on the model in force.
- Teacher binding is only as trustworthy as the platform's session identity.

## Disclosures (catalog rule 13)

- Network calls: **the plugin adds none of its own** (Hermes still sends
  guarded chat content to the operator-configured model provider, as for any
  chat). Credentials: **none**. Telemetry: **none**.
- Reads/writes: only `<hermes home>/plugin-data/struggle-guard/` (via plugin_db).
- No shell commands, no background processes, no self-updating code.
- Stores message excerpts (max 500 chars) of detected attempts locally.

## Testing

- `hermes plugins validate`: all checks pass (incl. security scan, no core
  override) on v0.22.7 and main @ 1fcc57e.
- `hermes plugins doctor --ci`: pass.
- tests/test_adversarial.py: 80 checks (attempt detection incl. natural
  phrasings with negation guards, anti-farming incl. reworded repeats,
  learner isolation, screening layers, attack phrasings, false-positive
  guards, plumbing) + documented residual misses. Multi-turn leakage
  measurement: 6/6 piecemeal extraction replies screened (offline, against
  injected responses).
- tests/test_integration.py: 63 checks through the real plugin loader, incl.
  fail-closed teacher identity, cross-learner isolation, role refusals,
  deterministic actor attribution and code-free fallbacks.
- Verified through the real PluginManager + middleware chains on v0.22.7.
- Live-verified 6 Nov 2026 against a real free model (OpenRouter free tier,
  $0-capped key): demands answered with one short level-1 question; observed
  attempts counted; a reworded repeat rejected as attempt_duplicate with the
  level held; a leaking reply screened and replaced with the deterministic
  fallback; full teacher report with per-event actor attribution.

## Demo

Video: [video link - filled at PR creation]. 83s. Every tutoring frame is an
edited capture of an actual CLI run against the real model (OpenRouter free
tier, Hermes v0.22.7, 6 Nov 2026) with a synthetic student: captured text
re-typeset for legibility, wording preserved, nothing staged; overlays come
from the actual struggle_guard_report JSON.
