# Deployment Profiles

Label-Forge Community Edition now groups deployment guidance by runtime profile
instead of the old `dev / stg / prod` labels.

## Canonical compose layout

The supported public compose path is:

- `infra/compose.base.yaml`
- `infra/compose.cpu.yaml`
- `infra/compose.gpu.yaml`
- `infra/compose.cloud.yaml`

Older legacy compose files were removed. Community Edition now supports only
the base-plus-profile layout above.

## Profiles

### `gpu`

- Primary local development profile on an NVIDIA GPU workstation
- Hot reload enabled
- SAM2 enabled by default
- Uses `requirements/gpu.txt`
- Requires the host to support `docker compose` GPU access

### `cpu`

- Fallback local development profile on a laptop or CPU-only workstation
- Hot reload enabled
- SAM2 disabled by default
- Uses `requirements/cpu.txt`

### `cloud`

- Single-host self-hosting recipe
- No source-code bind mounts
- No hot reload
- Bootstrap admin disabled by default until you opt in with unique credentials
- HTTPS-only session cookies enabled in the example env
- Defaults to CPU runtime unless you explicitly switch the env values and image
  build to a GPU-capable host

## Technical differences

### `gpu`

- `requirements/gpu.txt`
- `SAM2_ENABLED=1`
- `SAM2_DEVICE=cuda`
- `SAM2_REQUIRE_GPU=1`
- `gpus: all`
- bind mounts for source code and models
- `uvicorn --reload`

### `cpu`

- `requirements/cpu.txt`
- `SAM2_ENABLED=0`
- `SAM2_DEVICE=cpu`
- `SAM2_REQUIRE_GPU=0`
- no `gpus: all`
- bind mounts for source code
- `uvicorn --reload`

### `cloud`

- `ENV=prod` in the example env
- `requirements/cpu.txt` by default
- no code bind mounts
- no hot reload
- `BOOTSTRAP_DEFAULT_ADMIN_ENABLED=0` by default
- `SESSION_COOKIE_HTTPS_ONLY=1` in the example env
- proxy-header trust left disabled unless you explicitly configure
  `TRUST_PROXY_HEADERS` and `TRUSTED_PROXY_IPS`
- `CORS_ALLOW_ORIGINS` left empty unless you explicitly need cross-origin browser access
- `restart: unless-stopped`
- uploads stored in a named Docker volume
- CPU-first defaults unless you intentionally customize it

## Recommended workflow

1. Copy the matching example env file to `.env`.
2. Adjust secrets, ports, and database credentials.
3. Start the stack with `manage_label_forge.(sh|bat) up-build`.
4. Let the management script run `alembic upgrade head`.

The env file is the canonical profile selector. A CLI profile argument only
changes the compose overlay and should be treated as a temporary override.

## Storage note

The current public Community Edition does not yet ship an S3/object-storage
backend.

Current behavior:

- `gpu` and `cpu` store uploads through the local development bind mounts
- `cloud` stores uploads through a Docker-managed named volume
- no profile currently changes the media backend to S3

## Why this layout

The previous `dev / stg / prod` split mixed two different concerns:

- stage of deployment
- runtime shape and hardware assumptions

For public Community Edition onboarding, the runtime shape is the first
decision a developer needs to make, so the docs and compose files now follow
that path first.
