"""The audit log in PostgreSQL (Phase 9; interface design, Section 4; D-150 to D-153, D-159).

- `migrate`: the numbered SQL migrations in `migrations/` and their runner (schema `audit`,
  label `audit-schema/1`).
- `project`: the pure projection of one `gen-log/3` record (and its `Answer`) to audit rows.
- `store`: calls to the audit functions; the app holds no table privileges.
"""
