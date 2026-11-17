# Emu the Menu for the Traquenard Server: runs as user 10001 (not root) on port 8080, writes
# nothing at runtime, and reads its database from EMU_DATABASE_URL. It fits Kubernetes' Pod
# Security `restricted` and runs with a read-only root filesystem:
#   docker run --user 10001 --read-only --tmpfs /tmp --cap-drop ALL \
#     -e EMU_DATABASE_URL=postgresql://... -p 8080:8080 ghcr.io/bamk74/emu-the-menu:<tag>
# Both images exist for linux/arm64 and linux/amd64 (checked 2026-11-17 with
# `docker buildx imagetools inspect` and Docker Hub's tag API).

FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

# --- build: install the locked dependencies and emu into /app/.venv ----------------------------
FROM docker.io/library/python:3.13.18-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
# Use this image's Python, compile bytecode now (the running container cannot write it), and copy
# files rather than link them into the venv, since the cache does not reach the final image.
ENV UV_PYTHON_DOWNLOADS=0 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# --- run: the venv alone, on the same Python, as a numeric non-root user -------------------------
FROM docker.io/library/python:3.13.18-slim-trixie

# Links the GHCR package to the repo, and so makes it the package's source:
# https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
LABEL org.opencontainers.image.source="https://github.com/BAMK74/emu-the-menu" \
      org.opencontainers.image.description="Emu the Menu: a household stock and menu planner" \
      org.opencontainers.image.licenses="LicenseRef-PolyForm-Noncommercial-1.0.0"

COPY --from=build /app/.venv /app/.venv
COPY LICENSE.md NOTICE.md THIRD_PARTY.md /app/
COPY third_party /app/third_party

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001:10001
EXPOSE 8080

CMD ["uvicorn", "emu_the_menu.web:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", \
     "--no-server-header"]
