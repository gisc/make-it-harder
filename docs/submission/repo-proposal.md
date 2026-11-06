# DRAFT repo + license proposal (proposal only - no repo created, nothing public)

## Proposed public repo
- URL: https://github.com/gisc/make-it-harder (Main's proposal, 6 Nov; user approval pending)
- Name/description: "make-it-harder - Socratic tutoring guardrail plugin for Hermes (NousResearch Hermes Agent Plugin Hackathon entry)"
- Public visibility, MIT license, initial commit = current reviewed tree.

## Slug decision for the user
- Product/display name everywhere: Make It Harder.
- Internal technical slug stays `struggle-guard` (plugin directory name, tool
  names struggle_guard_*, config keys plugins.entries.struggle-guard) UNLESS
  the user approves a full rename. Renaming the plugin directory changes the
  catalog entry name and operator config keys; it is cosmetic but touches
  every doc. Recommendation: keep slug, rename only the repo.
- catalog-entry.yaml `name:` field stays struggle-guard until that decision.

## License recommendation: MIT
- All code is original (stdlib + Hermes plugin API only; no vendored or
  adapted third-party code), so any license is compatible.
- MIT is the norm for Hermes/NousResearch ecosystem plugins, keeps the
  catalog review trivial, and matches the hackathon's open-source spirit.
- LICENSE file drafted at repo root (copyright "2026 Soong Chee Gi") -
  confirm the copyright holder name with the user before publishing.
- Alternative if the user prefers patent language: Apache-2.0. No strong
  reason found to require it.

## Pre-public checklist (all pending)
- [ ] user approves repo creation, slug, license, copyright holder
- [ ] re-run secret/identifier scan on the exact committed tree
- [ ] remove or parameterize test-local /tmp paths (cosmetic; they are
      generic local paths, not private identifiers)
- [ ] pin exact commit SHA into catalog-entry.yaml after push
- [ ] confirm GitHub account gisc ownership with the user
