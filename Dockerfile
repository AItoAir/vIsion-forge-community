FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG LF_DOCKER_REQUIREMENTS_FILE=requirements/cpu.txt
ARG TORCH_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cu124

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    git \
    libpq-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements /app/requirements

RUN if [ ! -f "${LF_DOCKER_REQUIREMENTS_FILE}" ]; then \
      echo "Unknown requirements file: ${LF_DOCKER_REQUIREMENTS_FILE}" >&2; \
      exit 1; \
    fi \
 && if [ "${LF_DOCKER_REQUIREMENTS_FILE}" = "requirements/gpu.txt" ]; then \
      pip install --no-cache-dir --extra-index-url "${TORCH_EXTRA_INDEX_URL}" -r "${LF_DOCKER_REQUIREMENTS_FILE}"; \
    else \
      pip install --no-cache-dir -r "${LF_DOCKER_REQUIREMENTS_FILE}"; \
    fi

COPY LICENSE LICENSE.BSL-1.1 COMMERCIAL_LICENSE.md THIRD_PARTY_NOTICES.md MODEL_LICENSES.md README.md .env.example /app/
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY app /app/app
COPY static /app/static
COPY templates /app/templates

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
