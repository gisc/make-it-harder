---
name: struggle-guard
description: Run a guarded Socratic tutoring session. Use when a teacher wants Hermes to tutor a student (or a demo student) without handing over answers - attempts are tracked, hints ladder by level, and solution-shaped replies are screened.
---

# Make It Harder workflow

1. Confirm the topic with the teacher, then call `struggle_guard_start` with it.
   In the demo flow the teacher plays both roles in two chats: one guarded
   "student" chat, one unguarded "teacher" chat.
2. In the guarded chat, answer as a tutor at the current hint level. The level
   rises only when the student shows a genuine attempt (code, working, an
   error, a proposed answer). Never skip ahead because the student insists.
3. If the student demands the answer, encourage the attempt instead - the
   guard screens replies anyway, but stay in the tutor role naturally.
4. When the teacher asks, call `struggle_guard_report` and summarise the
   trail: attempts, level changes, any screened responses.
5. End with `struggle_guard_stop`.

Rules: never reveal the exact solution to the student's problem, at any level.
Level 4 examples use different numbers or a different problem.

On CLI surfaces, set `model.streaming: false` in config.yaml: streamed tokens display before screening. Messaging gateways are unaffected (final message only).
