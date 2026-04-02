# Database Migrations

FramePin Community Edition now ships with Alembic as the official migration
path.

## Canonical workflow

Use one of the management scripts:

- `./manage_frame_pin.sh migrate`
- `manage_frame_pin.bat migrate`

The scripts run:

```bash
alembic upgrade head
```

## Startup behavior

The application still keeps its lightweight runtime schema checks in
[app/main.py](../app/main.py) for backward compatibility with older internal
databases and disposable local environments.

Treat that runtime logic as a safety net, not as the primary migration
mechanism.

## Existing databases created before Alembic

The initial Alembic revision uses `Base.metadata.create_all(..., checkfirst=True)`.
That means:

- a fresh database is created from the current SQLAlchemy metadata
- an already-existing database can usually be stamped forward without table
  recreation failures

If you are upgrading a database from a pre-Alembic internal build, take a
backup first and then run the migration command through the management script.

## Policy for future schema changes

- Every schema change should get an Alembic revision.
- The management scripts should remain the default entry point for applying
  migrations in Docker-based environments.
- Runtime schema patching should only cover compatibility gaps, not replace
  migration reviews.
