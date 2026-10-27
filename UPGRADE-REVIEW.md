# Stable runtime candidate review

Candidate only. Do not deploy from this document. Production database migration,
transport safety and protected-state checks remain required.

## Immutable inputs

- Wrapper baseline: `0337856`, with only the Dockerfile and two YAML imports adapted.
- Old core: tag `v2026.7.20`, commit `3ef6bbd201263d354fd83ec55b3c306ded2eb72a`, CLI 0.19.0.
- Target: stable tag `v0.22.5`, commit `bf0203a794a07aac3ff764972ab9087baa4e4f4e`.
- Published image index: `sha256:9c5561579f3ffdf333401797094ac3a743738d6c25d40f4331c7bf77b694238d`.
- AMD64 image manifest: `sha256:fd822bae0a61bf023450737edbeaeea5fc6460f17cb4ee240a7e67b306ed63fb`.
- Coordinator's full installed-source inventory matched all 3,319 regular Python,
  shell, TOML and YAML files to the exact old commit, with no mismatches or extras
  (excluding dependency and Node module trees). This supersedes the nine-file sample.

The old Python 3.12 plus pip-based build is incompatible with the target's
supported Python 3.14/PM build. Core dependencies are gated to 3.14, and the old
`honcho`/`hindsight` extras no longer exist. The candidate consumes the published,
sealed PM runtime instead of recreating dependency and frontend assembly.

The existing wrapper remains the only gateway supervisor, under tini. The
upstream s6 entrypoint is overridden; its privilege-dropping CLI shim is excluded
from PATH because it resets HOME to a different volume path. The assembled CLI
and its Python environment remain upstream-owned. A path alias retains the
wrapper's old config-seed path. No model, group, roster, job or prompt is embedded.

The two wrapper `yaml` imports use upstream's `hermes_yaml` compatibility API.
Semantic configuration preservation passed. Serialized YAML whitespace can change,
so byte hashes alone cannot assess preserved configuration after startup.

## Database operations and rollback

Old schema version is 22; target is 32. Starting the target against the existing
store performs production writes. The exact SQL observed on a **synthetic**
schema-22 inline-FTS fixture is `tests/evidence/migration-statements.sql`.
It is evidence, not a script to apply manually. Runtime SQL is conditional on the
actual schema, indexes, row data and FTS layout.

Observed and source-derived operations include:

- Add current tables, columns, indexes and triggers through declared schema
  reconciliation. Some legacy primary-key shapes are rebuilt transactionally if
  found; these conditional branches were absent in the synthetic fixture.
- Deduplicate non-null session system prompts into `system_prompts`, save the
  content hash in `sessions.system_prompt_hash`, and set inline `system_prompt`
  to NULL. This is behavior-incompatible with the old reader.
- Backfill missing message UIDs in bounded chunks; add display/transcript and
  durable runtime-admission fields. Backfill marker writes persist.
- Advertise optional FTS storage optimization. Full inline-to-external FTS
  optimization is **not** automatic. External trigram layouts can instead get a
  source-filter/rebuild migration. Existing broad FTS update triggers are replaced.
- Stamp schema version 32 and database identity; database pragmas may enable WAL.
  Inspect and preserve the operator's configured journal policy.

The actual old `SessionDB.get_session` uses `SELECT *` and cannot resolve a prompt
hash. A code-only rollback therefore returns NULL prompt snapshots. A synthetic
reproduction demonstrated this failure.

`tests/rollback-current-prompts.sql` is the smallest reviewed repair for that
specific incompatibility. With all consumers stopped, it expands prompt hashes
from the **current** database and clears the corresponding hash, retaining all
rows and the prompt table. Clearing is needed so a subsequent old-runtime prompt
update is not masked by a stale hash on the next upgrade. Missing references must
block this procedure. Never reset schema_version or restore an old database
snapshot after new work has arrived.

The fixture verified this SQL, then the actual old SessionDB read/append/prompt
update/new-session path, then target reopening. All current/new rows were retained.
A second, independent cross-image check rebuilt the original wrapper with its
Python 3.12 base and exact old upstream commit. Python 3.12.12 old-runtime reads,
append, prompt update and new-session creation passed, followed by target Python
3.14.9 reopening. This also passed on an isolated private snapshot copy with all
original session/message values preserved; no private rows are stored here.
The original wrapper dependencies were freshly resolved, not a historical lock.
Existing durable admissions/executions and other new-runtime records require
separate inspection before any downgrade. The initial copied-state fixtures had
empty ledgers; a separate terminal-ledger fixture below tests their retained shape.

## Local verification

- AMD64 candidate Docker build passed, including immutable commit/Python assertions.
- CLI reports v0.22.5, upstream bf0203a7, Docker installation, Python 3.14.9.
- Wrapper import, compiled dashboard/TUI and semantic config preservation passed.
- 729 brain checks in 32 suites passed in the candidate with network disabled.
  The test source was a tracked archive at the revision in
  `tests/brain-fixture-revision.txt`; no live credentials or production volume mounted.
- Actual target schema migration: two sessions/two messages retained, prompt
  deduplication and UID backfill passed, SQLite integrity OK.
- Scoped current-row rollback/old append/target reopen: three sessions/four messages,
  updated prompts retained, SQLite integrity OK.
- Separate-image Python 3.12 rollback: synthetic fixture finished with four sessions
  and five messages, retaining original and new rows and updated prompts. Evidence:
  `tests/evidence/cross-image-synthetic.json`. The actual-shape private-copy check
  also passed; its metadata-only report stays outside this public template.
- Terminal-ledger rollback fixture passed actual old gateway `SessionStore`
  reads/appends, manual reset, idle expiry, expiry finalization and new-session
  append, followed by target reopen. All terminal admission/execution/receipt rows
  remained unchanged. Actual delete, bulk-delete and prune were blocked by the
  guard and their transactions retained every message. Evidence:
  `tests/evidence/terminal-ledger-rollback.json`.
- Independent imported-runtime probes passed actual plugin discovery from tracked
  manifests/config: all four plugins, 19 tools and expected hooks loaded. Provider
  credentials were synthetic and no external services were started.
- Actual saved toolsets resolved to their explicit Telegram and cron allowlists;
  an empty cron allowlist stayed empty. Real pre-dispatch/lifecycle/triage suppressed
  18 synthetic client variants while permitting a staff DM.
- The actual scheduler builder/scanner accepted the full saved prompt, empty drain
  stdout skipped execution, and exactly one handoff marker survived real output
  saving, queue ownership verification and response extraction. No agent/model path
  was called. This replaces the earlier pending scheduler-import check.
- `git diff --check` passed.

## Required gates still open

The imported adapter probes reproduced the existing complete-receive/silence
gaps: unknown senders are rejected before observation under staff-only authorization,
forum command handling attempts command registration before triage, and typing
attempts a transport action. Rejected test transports prevented delivery. These
findings are not an implemented receive-only policy or a passing silence gate.
The complete media/lifecycle/scheduled outbound surface and any policy repairs
need their own scoped checks. Never widen staff permissions to admit observation.

New messaging hooks run before gateway authorization but after adapter prefilters;
they do not rerun while an already admitted durable row executes. Refuse rollback
if target admissions, executions, receipts or leases have unresolved ownership or
status. Terminal-ledger reset/read/append tests do not establish that the old
runtime can safely resume or reconcile in-flight ownership. Preserve these rows;
do not delete them to make a downgrade appear safe.
Even terminal admission/execution rows activate the new session-deletion guard;
old session deletion remains incompatible and upstream explicitly labels this
downgrade unsupported. The reviewed prompt restoration does not remove the guard.
Ordinary tested Telegram resets and expiry rotate sessions instead of deleting
them and passed with terminal rows. Full old-version feature parity is not claimed.

The actual snapshot-copy migration/conditional-shape review is complete; the
operator review artifact records its exact observed scope. Before production,
present that database write scope, obtain the applicable approval, verify a quiet boundary,
stop all old/new consumers, preserve a consistent current backup, and retain all
queue/claim/receipt state. After deployment read back protected settings, histories,
plugins, prompt/KB parity, Telegram state and natural scheduled ticks. No live
message, paid inference or historical work replay follows from this candidate.

## Reproduce

```sh
docker build --platform linux/amd64 -t dns139-template-candidate .
docker run --rm --platform linux/amd64 --network none --entrypoint python \
  -v "$PWD/tests:/checks:ro" dns139-template-candidate /checks/check_wrapper_contract.py
```

Migration check requires a public old-source archive under `tests/legacy-source`
and writes only synthetic evidence to the mounted output directory. The archived
brain and old source are excluded from Git and the Docker build context.
