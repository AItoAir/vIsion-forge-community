from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_BOOTSTRAP_ADMIN_EMAIL = "admin@visionforge.test"
DEFAULT_BOOTSTRAP_ADMIN_PASSWORD = "VisionForge123"
VALID_SESSION_COOKIE_SAME_SITE_VALUES = {"lax", "strict", "none"}


def normalized_session_cookie_same_site(value: str | None) -> str:
    normalized = (value or "lax").strip().lower() or "lax"
    if normalized not in VALID_SESSION_COOKIE_SAME_SITE_VALUES:
        allowed = ", ".join(sorted(VALID_SESSION_COOKIE_SAME_SITE_VALUES))
        raise RuntimeError(
            f"SESSION_COOKIE_SAME_SITE must be one of: {allowed}."
        )
    return normalized


def configured_trusted_proxy_hosts(value: str | None) -> list[str] | str:
    hosts = [host.strip() for host in (value or "").split(",") if host.strip()]
    if not hosts:
        raise RuntimeError(
            "TRUSTED_PROXY_IPS must list at least one exact proxy IP or '*'."
        )
    if "*" in hosts:
        return "*"
    return hosts


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+psycopg2://vision_forge:secret@localhost:5432/vision_forge"
    secret_key: str = "dev-insecure-session-key-change-before-production"
    password_salt: str = ""
    session_cookie_name: str = "vision_forge_session"
    session_cookie_https_only: bool = False
    session_cookie_same_site: str = "lax"
    session_cookie_max_age_seconds: int = 1209600
    trust_proxy_headers: bool = False
    trusted_proxy_ips: str = "127.0.0.1"
    cors_allow_origins: str = ""
    app_extension_hooks: str = ""
    bootstrap_default_admin_enabled: bool = False
    bootstrap_default_admin_email: str | None = None
    bootstrap_default_admin_password: str | None = None
    sam2_enabled: bool = False
    sam2_model_cfg: str = "configs/sam2.1/sam2.1_hiera_l.yaml"
    sam2_checkpoint: str = "/app/models/sam2.1_hiera_large.pt"
    sam2_device: str = "cuda"
    sam2_require_gpu: bool = True
    sam2_offload_video_to_cpu: bool = False
    sam2_offload_state_to_cpu: bool = False
    sam2_async_loading_frames: bool = False
    sam2_apply_postprocessing: bool = True
    sam2_vos_optimized: bool = False
    sam2_cache_dir: str = "/tmp/vision_forge_sam2"
    sam2_polygon_epsilon: float = 0.003
    sam2_video_chunk_size: int = 240
    sam2_video_chunk_overlap: int = 32
    sam2_video_chunk_threshold_frames: int = 320
    sam2_max_concurrent_jobs: int = 1
    sam2_max_queue_size: int = 8
    sam2_job_poll_interval_ms: int = 5000
    labeling_proxy_enabled: bool = True
    labeling_proxy_crf: int = 12
    labeling_proxy_preset: str = "veryfast"
    labeling_proxy_max_width: int = 0
    labeling_proxy_gop_size: int = 6
    labeling_proxy_b_frames: int = 0
    labeling_proxy_max_concurrent_jobs: int = 2
    labeling_proxy_storage_budget_gb: float = 100.0
    labeling_proxy_storage_ttl_days: int = 14

settings = Settings()
