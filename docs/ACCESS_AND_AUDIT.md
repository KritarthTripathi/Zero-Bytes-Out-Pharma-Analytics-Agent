# Access control, audit trail, pinned environment and UI

Everything here is demonstrated on synthetic data. It is GxP-aligned in design (attributable actions, access control, tamper-evident
records, controlled configuration), not validated or compliant.

## Roles and data scope

| User | Role | Sees | Geo levels it may compare | Clinical agent |
|---|---|---|---|---|
| tm_demo | tm (territory manager) | one territory | TERRITORY | no |
| dm_demo | dm (district manager) | one district | TERRITORY, DISTRICT | no |
| am_demo | am (area manager) | one area | TERRITORY, DISTRICT, AREA | no |
| admin_demo | admin | everything, plus the audit log | all | yes |

The TM demo user is bound to the busiest territory of "Cal1 district", so the DM and AM scopes contain the TM's data.

## How the scope is enforced (three layers, none of them trusts the LLM)

1. **Planner (answers "automatically scoped" and refuses clearly).** A question that names a territory, district, area, territory manager
   or HCP outside the user's scope is refused before any model call. A comparison above the user's level (a TM asking "which area...",
   a DM asking "which area...") is refused with the levels the user can compare. A division question is answered inside the scope
   (division totals computed from the user's own territories). A question without a place ("TRx in the latest month") is summed over the
   user's own scope.
2. **Database (the real enforcement).** For a restricted user the model's SQL runs in an in-memory DuckDB that contains only
   scope-filtered views of the approved relations; the real file is attached read-only. Geo views also hide the levels above the user.
3. **SQL guard.** Schema-qualified names such as `base.v_hcp_rx_latest` are rejected, so the unfiltered data cannot be reached around the views.

The model never sees the scope: the filter is invisible to it, so it cannot be asked to remove it.

## Audit trail

`data/audit/audit.jsonl`, append-only. Each record holds a sequence number, UTC time, event, user (name, role, scope), details and an
HMAC-SHA256 over the record plus the previous record's hash, keyed with `secrets/audit_hmac_key`. Editing, deleting or re-ordering a record
breaks the chain; without the key a valid chain cannot be recomputed.

Events: `login_success`, `login_failure` (with reason), `logout`, `question`, `access_denied`, `feedback`, `user_created`.
A question record stores the masked question, status, route decisions, SQL, row count, a SHA-256 of the result, duration, the pinned
environment hash and the code version. It never stores real names or result rows.

Commands: `python -m app.common.audit verify | head | tail 20`. Limit: removing the END of the file is only detectable if you keep
the `head` hash somewhere else (a note, a git commit message). The key holder can rewrite the whole chain.

## Pinned environment

`scripts/pin_environment.py` writes `data/environment/manifest.json`: Python and package versions, the model digests reported by Ollama,
and the hashes of `metrics.yml`, `agents.yml`, `joins.yml`, the SQL files and the evaluation sets. At startup `ENV_CHECK=warn` (default)
prints and records any difference; `ENV_CHECK=strict` refuses to start. A changed configuration or evaluation file means the evaluations
must be re-run (a simple change-control trigger). `lock_images.sh` and `lock_requirements.sh` record image IDs and exact package versions.

## UI

Streamlit page behind a login (scrypt password hashes, lock after 5 failures for 15 minutes, 30 minute idle timeout). It shows the answer,
a table, the route, the plan and the SQL, the scope note, and helpful / not helpful buttons (recorded in the audit log).

## Limits to state honestly

- Streamlit is a demo front end: one model call at a time, no password reset, no multi-factor, chat history only in memory.
- The audit key and the vault key sit on the same machine as the data; production would use a key-management service.
- The refusal messages repeat what the user typed; they do not reveal other people's data.
- Not covered: electronic signatures, validation documents, SOPs and change-control records (next steps).

## Evidence produced when building this

- Leak test over all 32 test questions with each role's scoped connection: 0 values outside the user's scope for tm, dm and am.
  Answered / refused: tm 9 / 23, dm 15 / 17, am 29 / 3 (refusals are mostly questions naming places or levels above the role).
- A district manager's "TRx in the latest month" (summed from the scoped territory rows) equals the administrator's district row.
