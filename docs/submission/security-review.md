# Source security review - 6 Nov 2026 (pre-public recommendation)

Scanned the full tree (struggle.py, __init__.py, plugin.yaml, skills/, docs/,
tests/, README.md, LICENSE) for secrets and private identifiers:

- No API keys, tokens, passwords, or credentials. The OpenRouter test key
  lives only in the vault and /tmp/recon/hermes-live/.env - never in the repo.
- No personal identifiers: no email addresses, phone numbers, real names
  beyond the drafted LICENSE copyright line (pending user confirmation).
- No live session ids (teacher/student cli:local-* ids appear only in
  /tmp/demo_live captures, which are NOT part of the repo).
- Local paths in tests (HERMES_SRC default /tmp/hermes-src, load path
  /tmp/recon/struggle-guard): generic machine-local paths, no private content;
  flagged as cosmetic cleanup in the repo proposal.
- Dependencies: Python stdlib only (hashlib, json, logging, os, re, sqlite3,
  sys, tempfile, textwrap, time, typing, pathlib); Hermes plugin API
  (plugins.plugin_loader) in tests only. No third-party packages, no
  borrowed/vendored code - license compatibility is unrestricted.
- Runtime disclosures for the catalog: the plugin adds no network calls,
  credentials or telemetry of its own (Hermes still sends guarded chat
  content to the configured model provider); writes only under <hermes home>/plugin-data/struggle-guard/;
  stores local message excerpts (max 500 chars) of detected attempts.

Verdict: clean for public release pending the repo-proposal checklist.
