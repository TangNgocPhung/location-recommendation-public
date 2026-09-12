# Database lifecycle

Database objects are managed by Alembic under `backend/migrations/`. The former
`database/init/*.sql` bootstrap files were removed so schema changes are ordered,
repeatable and visible in the `alembic_version` table.

Docker Compose runs the `migrate` service before the API and stream worker. For
manual migration commands, run them from `backend/` with `DATABASE_URL` set:

```bash
alembic current
alembic upgrade head
alembic downgrade -1
```
