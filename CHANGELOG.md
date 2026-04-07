# Changelog

All notable Community Edition release changes are documented in this file.

## Unreleased

## v0.1.3 - 2026-04-07

Compared with `v0.1.2`, this release mainly adds:

- DICOM and NIfTI medical-image ingest that converts supported uploads into annotation-ready PNG or MP4 previews while preserving the original source file for export.
- Safe project deletion with full cleanup, configurable team active-user seat limits, and sturdier upload validation and preview fallbacks.
- Annotation workspace updates for quieter teammate presence, faster autosave, unsaved polygon guards, and browser-safe HEIC display fallbacks.
- New export tooling for basename-only project item paths and `lf_project_v2` to COCO conversion.
- Follow-up patch: team invite redirects now preserve the active-user seat-limit error state so admins see the correct team settings warning when the limit is reached.
- Follow-up patch: the project delete confirmation modal now uses the correct singular or plural copy when it summarizes hidden sample files.

## v0.1.2 - 2026-04-02

Compared with `v0.1.1`, this release mainly adds:

- Team member role updates, inactive-member visibility, and safer removal controls in team settings.
- Public API v1 endpoints, related database tables, export job plumbing, and webhook support for integration workflows.
- Updated my-page and API-key flows, plus harder media-state handling around converted-video labeling.
- Final FramePin branding updates across the app, startup scripts, env defaults, and public documentation.

## v0.1.1 - 2026-03-31

Compared with `v0.1.0`, this release mainly adds:

- Project item search and filter controls, with per-item label summaries that show object and frame counts.
- In-app notifications and `@mention` support for annotation comments, including the required database migrations.
- More reliable live collaboration and review behavior, including presence sync fixes, same-origin websocket handling, and smoother frame-step navigation.
- Labeling UI performance work for interpolation and canvas resizing, including preserved annotation alignment when the viewport changes size.
- Updated README demo media for SAM2 object masking and live collaboration.

## v0.1.0 - 2026-03-29

- First public FramePin Community Edition release.
- Public runtime profiles for `cpu`, `gpu`, and `cloud`, with `.env` as the canonical runtime configuration file.
- Alembic-based startup migrations and the browser-played converted-video labeling flow for video items.
