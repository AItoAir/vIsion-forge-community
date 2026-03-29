# Contributing

Thanks for contributing to Label-Forge Community Edition.

## Development flow

1. Choose a runtime profile by copying one of the `.env.*.example` files to
   `.env`.
2. Start the stack with `manage_label_forge.(sh|bat) up-build`.
3. If your change touches schema, add an Alembic revision.
4. Keep public-repo boundaries intact. Do not add private checkpoints,
   customer data, or enterprise-only modules.

## Pull request expectations

- Keep changes scoped.
- Document any new environment variables in `.env.example` and the matching
  profile examples.
- Document any new deployment behavior in `README.md` or `docs/`.
- Mention migration impact whenever a schema-related change is included.

## Validation

Use Python 3.11 locally for validation so your environment matches CI and the
published Docker image.

Before opening a PR, run:

```bash
python -m compileall app alembic
python -m unittest discover -s tests -p "test_*.py" -v
python tools/check_public_release.py
```

If your change depends on Docker behavior, also run the relevant
`manage_label_forge` command for the profile you changed.
