# Make It Harder (`struggle-guard`)

*"Life is hard. Why not make it harder."*

A Hermes plugin that turns a chat into a guarded Socratic tutoring session.
Its measured value is **hint progression gated on counted, logged attempts -
the level comes from database state, not from how a request is phrased - and
a teacher-visible learning trail**. Its answer-leak screening is a heuristic
guardrail with measured misses (below), not reliable answer prevention.

The operator pins teacher session identities in config
(`plugins.entries.struggle-guard.settings.teacher_sessions`); without that
config every privileged tool fails closed. From a bound teacher chat,
`struggle_guard_status` lists the session keys Hermes has seen, and
`struggle_guard_start` arms a guarded session for a *named* student chat.
No chat is ever guarded unless a teacher names it. From then on:

- **Attempts are counted in code, not by prompt.** Genuine-looking student
  attempts (pasted code, an error with context, their working, a proposed
  answer or a proposed approach in words) are detected in `llm_request` middleware and stored in `plugin_db`.
  The hint level is `min(1 + attempts, 4)` - deterministic state, not an
  instruction the model can be talked out of. Attempts are *observed
  messages, not verified learning*: a student who fabricates plausible
  attempt messages can climb the ladder, and every attempt is logged for the
  teacher to judge.
- **Anti-farming.** An attempt only counts if it carries substance (2+ content
  tokens or pasted code), is not an exact resend, and is not the same work
  restated: near-duplicates are caught two ways - overall wording overlap
  (Jaccard >= 0.7), or same concrete anchors (code tokens like `a[j+1]` and
  test data) with similar wording (Jaccard >= 0.4) and no new anchor. A delta
  of pure numbers alone (rewording the same proposal with a tweaked value)
  also counts as the same work when the wording stays similar. This
  catches the reworded repeats we measured; broader fabrication or thorough
  rewording with fresh anchors can still count - attempts remain observed
  messages, not verified learning, and every duplicate is logged as
  `attempt_duplicate` so the teacher sees the try.
- **Hints ladder by level.** Level 1 asks one Socratic question; level 2 gives
  a conceptual hint; level 3 allows a short structural outline (<=3 lines);
  level 4 - the top of the ladder - allows a small worked example on
  *different* data (<=8 illustrative lines). The exact solution to the
  student's own problem is never the permitted output at any level - that is
  the screening policy; its measured misses are published below.
- **Responses are screened.** `llm_execution` middleware scans each reply for
  solution-shaped content beyond the current level: solution phrases, fenced
  code over allowance, *unfenced* code lines, inline code spans, step-by-step
  worked solutions, and prose recipes (3+ distinct procedure verbs at L1-L2).
  A violation is replaced with a level-appropriate fallback message.
- **Teacher console.** Teacher identity is bound to configured runtime
  session ids, never to anything a user types. `start`, `stop` and `report`
  only run from a configured teacher session: a student chat cannot arm,
  stop its own guard, stop another learner, or read any report, even by
  telling the model to call the tool. Guards key on `platform:session_id`;
  chats without a session identity are refused, so two learners never share
  state.
- **The teacher gets the trail.** Every attempt, duplicate, level change, API
  call and screening lands in a local `struggle_log` table;
  `struggle_guard_report` summarises it.

## Tools

| Tool | Purpose |
| --- | --- |
| `struggle_guard_start` | Arm a guard on a *named* student chat, by session key (teacher chats only) |
| `struggle_guard_status` | Current attempts and hint level for the calling chat (unprivileged) |
| `struggle_guard_report` | Teacher view of the learning trail (teacher chats only) |
| `struggle_guard_stop` | Stop a guarded chat, by session key (teacher chats only) |

## Streaming surfaces (CLI)

**CLI operators: set `model.streaming: false` in config.yaml before guarding
a chat.** An interactive CLI streams tokens as they arrive; without that
setting, raw unscreened tokens can display before the screen runs (the trail
still records the screening). Screening itself replaces the assistant's reply
BEFORE it is finalized. Messaging gateways (Telegram/Discord/WhatsApp) are
expected to deliver only the final, screened message, but no gateway
transport has been tested - only the CLI surface is verified.

## Honest limits

- **Hermes middleware is fail-open.** If this plugin errors, Hermes logs a
  warning and continues *without any screening*. The plugin's own handlers
  catch their exceptions for the same reason. Nothing here guarantees the
  screen ran.
- **Screening is heuristic, and the misses are measured, not hidden.**
  `tests/test_adversarial.py` prints every observed miss as `MISS` and the
  suite fails while any miss from the core attack set remains unaddressed.
  Residual known misses live in the `RESIDUAL` section of the test output and
  in release notes - currently: heavily paraphrased one-verb recipes, and
  answers split across multiple short replies.
- **No regeneration.** Hermes middleware `next_call` is single-use per frame,
  so a screened response is *replaced* with a fallback, not regenerated.
- Attempt detection is keyword/shape based; an unusual genuine attempt can be
  missed (the level just climbs slower), and a pasted question with
  attempt-like wording can be credited.
- Role separation binds teacher actions to the runtime session identity the
  operator configures. It is only as trustworthy as the platform supplying
  that identity: it stops students driving teacher tools through the model,
  but it is not cryptographic authentication.

The adversarial suite in `tests/` measures all of the above against concrete
attack phrasings; results are published with each release.

## Privacy

All state stays on-device in `<hermes home>/plugin-data/struggle-guard/`.
The plugin adds no network calls, credentials or telemetry of its own;
Hermes itself still sends guarded chat content to the operator-configured
model provider, as it does for any chat. Demo and classroom trials should use
synthetic students; the plugin stores message excerpts (max 500 chars) of
detected attempts in the local log.
