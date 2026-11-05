# Users client rules

- New users clients under `clients/` must call `/api/v2/users` (ADR-002).
- Clients under `legacy/` may keep using `/api/v1/users` during the migration.
- Update the relevant tests when changing a client route.
