# Third-Party Notices

This repository contains first-party Label-Forge Community Edition code and also
depends on third-party software.

The Label-Forge Community Edition license applies only to first-party material in
this repository unless a file or directory states otherwise. Third-party software,
third-party assets, and third-party notices remain under their own original terms.

## Principal build-time dependencies

The default application build references or installs software including, but not
limited to:

- FastAPI and Starlette
- SQLAlchemy
- Pydantic and pydantic-settings
- Jinja2
- structlog
- psycopg2-binary
- NumPy
- OpenCV
- PyTorch and TorchVision
- Hydra Core
- iopath

These dependencies remain under their own upstream licenses. Preserve their
required copyright statements, notices, and license texts when redistributing
source archives, installers, containers, or other binaries.

## SAM 2

The default Docker build installs SAM 2 from the upstream
`facebookresearch/sam2` repository.

SAM 2 code, model checkpoints, optional connected-component code, fonts, and
other bundled or referenced upstream assets remain under their own upstream
license terms and notices. Do not remove or overwrite those upstream notices
when redistributing builds that include or depend on SAM 2.

## Redistribution rule

If you distribute this software in any form, preserve all third-party license
texts, attribution notices, and copyright statements required by the original
licensors.
