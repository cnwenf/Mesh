# Mesh backend image — serves API / worker / realtime gateway via different commands.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir .

# Run as a non-root user.
RUN useradd --create-home mesh
USER mesh

EXPOSE 8000 8081

# Default: API server. Override with `python -m mesh.workers` (worker) or
# `uvicorn mesh.realtime.app:create_app --factory` (realtime gateway).
CMD ["uvicorn", "mesh.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
