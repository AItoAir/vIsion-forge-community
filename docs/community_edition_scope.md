# Community Edition Scope

This document makes the public repository boundary explicit for developers and
evaluators.

## Included in Community Edition

- first-party FastAPI application code
- first-party templates, CSS, and JavaScript assets
- project-authored export formats and documentation
- Docker-based local and single-host deployment recipes
- Alembic migration scaffolding for the public schema

## Not included in Community Edition

- private enterprise modules
- OEM, redistribution, embedding, and white-label rights
- commercial support deliverables
- customer datasets or customer exports
- private checkpoints or private model packages

## Runtime feature boundary

### GPU profile

- intended as the primary local development path
- intended for teams evaluating SAM2-assisted workflows
- requires an NVIDIA-capable Docker host
- uses the GPU-focused dependency set

### CPU profile

- intended as the fallback for evaluation, local development, and UI work
- SAM2 disabled by default
- lowest-friction path for contributors without CUDA access

### Cloud profile

- intended for single-host self-hosting
- no hot reload
- persistent uploads volume
- defaults to CPU-safe settings unless you explicitly enable GPU requirements

## Storage boundary

The current public repository does not yet include an S3/object-storage backend.

## Medical imaging support boundary

Community Edition supports medical-image ingest for annotation workflows through
rendered 2D previews.

Supported source inputs:

- `.dcm` / `.dicom`
- `.zip` containing a single DICOM series
- `.nii`
- `.nii.gz`

Current behavior:

- single-frame studies are rendered as image items
- multi-frame or volumetric studies are rendered as video-style slice previews
- medical previews use the same labeling workspace and review flow as standard image and video uploads
- the original uploaded source file remains available for export and traceability

Not included in this support boundary:

- native 3D volume editing
- archive uploads containing multiple DICOM series
- native DICOM or NIfTI annotation export formats

## Practical support boundary

Community Edition should aim to be:

- easy to clone
- easy to start
- explicit about environment variables
- explicit about migration behavior

Enterprise-only deployment or support entitlements should stay outside this
repository and be described through your commercial licensing material, not
through hidden behavior in the public tree.

## Private overlay seam

The public startup path is intentionally split into core registration functions
in `app/main.py`.

Private overlays should register extra middleware, mounts, or routers through
`APP_EXTENSION_HOOKS` modules that expose `apply_extension_hooks(app)` instead
of patching the Community Edition app factory directly.
