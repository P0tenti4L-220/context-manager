# ADR-002: Use Users API v2 for new clients

Status: accepted. Effective: 2026-10-01. Supersedes: ADR-001 for `clients/`.

New users clients under `clients/` must call `/api/v2/users`.
Existing clients under `legacy/` may keep calling `/api/v1/users` during the migration.
