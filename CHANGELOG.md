# Changelog

All notable Community Edition release changes are documented in this file.

## v0.1.1 - 2026-03-31

Compared with `v0.1.0`, this release mainly adds:

- Project item search and filter controls, with per-item label summaries that show object and frame counts.
- In-app notifications and `@mention` support for annotation comments, including the required database migrations.
- More reliable live collaboration and review behavior, including presence sync fixes, same-origin websocket handling, and smoother frame-step navigation.
- Labeling UI performance work for interpolation and canvas resizing, including preserved annotation alignment when the viewport changes size.
- Updated README demo media for SAM2 object masking and live collaboration.

## v0.1.0 - 2026-03-29

- First public VisionForge Community Edition release.
- Public runtime profiles for `cpu`, `gpu`, and `cloud`, with `.env` as the canonical runtime configuration file.
- Alembic-based startup migrations and the browser-played converted-video labeling flow for video items.
