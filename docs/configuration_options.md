# Configuration Options

FramePin Community Edition uses the `.env` file as the main configuration
surface.

## The first decision

Pick one profile first:

- `gpu`: primary local-development path with SAM2 enabled
- `cpu`: fallback local-development path without CUDA
- `cloud`: single-host self-hosting path

The matching templates are:

- `.env.example` or `.env.gpu.example`
- `.env.cpu.example`
- `.env.cloud.example`

## Main env groups

### Profile and startup

- `LF_RUNTIME_PROFILE`
- `LF_PROJECT_NAME`
- `LF_DOCKER_REQUIREMENTS_FILE`
- `LF_PUBLIC_PORT`
- `LF_DATABASE_HOST_PORT`
- `LF_RUN_MIGRATIONS_ON_START`

### Security and sessions

- `ENV`
- `SECRET_KEY`
- `PASSWORD_SALT`
- `SESSION_COOKIE_NAME`
- `SESSION_COOKIE_HTTPS_ONLY`
- `SESSION_COOKIE_SAME_SITE`
- `SESSION_COOKIE_MAX_AGE_SECONDS`
- `TRUST_PROXY_HEADERS`
- `TRUSTED_PROXY_IPS`
- `CORS_ALLOW_ORIGINS`

### Optional private overlays

- `APP_EXTENSION_HOOKS`

### Database

- `LF_DATABASE_NAME`
- `LF_DATABASE_USER`
- `LF_DATABASE_PASSWORD`
- `DATABASE_URL`

### Bootstrap admin

- `BOOTSTRAP_DEFAULT_ADMIN_ENABLED`
- `BOOTSTRAP_DEFAULT_ADMIN_EMAIL`
- `BOOTSTRAP_DEFAULT_ADMIN_PASSWORD`

The local-development templates enable a repository-default bootstrap admin for
loopback evaluation. The cloud template leaves bootstrap admin disabled by
default and expects you to opt in with unique credentials only long enough to
seed the first admin account.

`PASSWORD_SALT` is now only needed when upgrading older deployments that still
store legacy SHA-256 password hashes. Fresh Argon2id password hashes do not use
that setting.

### SAM2 and inference

- `SAM2_ENABLED`
- `SAM2_MODEL_CFG`
- `SAM2_CHECKPOINT`
- `SAM2_DEVICE`
- `SAM2_REQUIRE_GPU`
- `SAM2_OFFLOAD_VIDEO_TO_CPU`
- `SAM2_OFFLOAD_STATE_TO_CPU`
- `SAM2_ASYNC_LOADING_FRAMES`
- `SAM2_APPLY_POSTPROCESSING`
- `SAM2_VOS_OPTIMIZED`
- `SAM2_CACHE_DIR`
- `SAM2_POLYGON_EPSILON`
- `SAM2_VIDEO_CHUNK_SIZE`
- `SAM2_VIDEO_CHUNK_OVERLAP`
- `SAM2_VIDEO_CHUNK_THRESHOLD_FRAMES`

## Reading the options correctly

- `gpu` and `cpu` are mainly local-development hardware presets
- `cloud` is mainly an operational preset for single-host deployment
- `cloud` is not yet a cloud-vendor integration and does not imply S3, CDN,
  managed Postgres, or Kubernetes
- `cloud` keeps `SESSION_COOKIE_HTTPS_ONLY=1` in the example env so secure
  cookies are explicit by default for self-hosted HTTPS deployments
- leave `CORS_ALLOW_ORIGINS` empty for same-origin browser access; when you do
  need cross-origin browser access, set an explicit allowlist instead of `*`
- enable `TRUST_PROXY_HEADERS` only when a trusted reverse proxy sets
  `X-Forwarded-Proto` or `X-Forwarded-For`, and keep `TRUSTED_PROXY_IPS`
  limited to those exact proxy IPs unless you intentionally choose `*`
