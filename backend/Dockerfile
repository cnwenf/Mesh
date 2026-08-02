# Mesh backend image — serves API / worker / realtime gateway via different commands.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md requirements.lock ./

# Reproducible image: runtime deps come from the committed, hash-pinned
# lockfile; the project itself is then installed without re-resolving.
# Dependency install is ordered before the source copies so routine source
# edits reuse the cached dependency layer.
RUN pip install --no-cache-dir -r requirements.lock

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

# --no-deps: the lockfile above is authoritative; pip check then fails the
# build if the lockfile ever stops satisfying pyproject (drift gate).
RUN pip install --no-cache-dir --no-deps . \
 && pip check

# Run as a non-root user.
RUN useradd --create-home mesh
USER mesh

EXPOSE 8000 8081

# Default: API server. Override with `python -m mesh.workers` (worker) or
# `uvicorn mesh.realtime.app:create_app --factory` (realtime gateway).
CMD ["uvicorn", "mesh.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
