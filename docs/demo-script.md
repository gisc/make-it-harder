# Make It Harder - demo video script (v3, PRIVATE DRAFT)

Target length: 75-85 seconds. Audience: hackathon judges + teachers.
Rule: the entry video must show the REAL model run. Every tutoring scene below
is live footage of Hermes v0.22.7 + struggle-guard on the real provider. No
scripted model responses anywhere in the entry cut. A screen-firing moment is
reliably elicited live by asking for "skeleton code with blanks" at level 3:
the model starts writing code, the guard swaps in the level-appropriate
fallback, and the trail logs the screened reply with its reason.

## Scene 1 - the problem (0:00-0:10)
Visual: student pastes homework into a generic chatbot; full solution returns
instantly.
VO: "The answer is one prompt away. The struggle - where learning happens -
is gone."

## Scene 2 - setup (0:10-0:25) [LIVE]
Visual: teacher's chat. `struggle_guard_status` shows seen sessions; teacher
arms "Aiden (demo student)" on the bubble sort trace.
VO: "Make It Harder is a Hermes plugin. The teacher guards a student's
chat. The rules live in code, not in the prompt."

## Scene 3 - demand fails, attempts climb (0:25-0:50) [LIVE]
Visual: student chat, rapid cuts: "just give me the answer" -> one short
grounded question (level 1/4). Student describes their loop + test result ->
conceptual hint (level 2/4). Reworded repeat of the same attempt -> rejected,
level held (overlay: "same work restated"). Genuinely new attempt ->
structural outline (level 3/4). "Give me the skeleton code" -> model leaks,
screen swaps in the level-appropriate fallback before it lands.
VO: "Demanding the answer gets a question. Each observed attempt climbs one
rung: question, concept, structure, worked example on different data. The
ladder is database state - resend or reword the same work and it doesn't
climb. The teacher sees the rejection."

## Scene 4 - teacher trail (0:50-1:05) [LIVE]
Visual: teacher runs `struggle_guard_report`: attempts, level changes, a
screened response with its reason, the rejected duplicate.
VO: "And the teacher sees the whole trail: what the student tried, when hints
climbed, and every time the guard screened a reply that gave away too much."

## Scene 5 - honesty + close (1:05-1:20)
Visual: README honest-limits section, one breath on screen.
VO: "It's a guardrail with measured limits, not a guarantee - but the ladder
is code, attempts are counted, and the teacher sees everything. Life is hard.
Why not make it harder. That's where the learning is."
End card: "Make It Harder" + tagline "Life is hard. Why not make it harder." (period, as written), repo link, "Hermes Agent Plugin Hackathon".

## Production notes
- Backbone: real provider (see test plan); Hermes v0.22.7; two CLI sessions
  with stable session ids, or two Telegram demo accounts if messaging UX
  films better.
- Teacher identity: config teacher_sessions pinned before recording.
- Overlays rendered from `struggle_guard_status` JSON, composited in post.
- Capture 2 takes max per scene to stay inside free-tier request limits.
