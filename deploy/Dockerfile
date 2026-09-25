# FastH3 Comfy Server - professional API layer (uv-managed environment)
#
# The official uv image already contains a uv-managed CPython 3.11, so no
# system Python or venv tooling is required. Dependencies are installed from
# the hashed lockfile for fully reproducible builds.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    FASTH3_DATA_DIR=/srv/fasth3/data

WORKDIR /srv/fasth3

# 1) Dependencies first (best layer caching): lockfile-driven, no resolver drift.
COPY server/requirements.txt server/requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /srv/fasth3/.venv \
 && uv pip sync --python /srv/fasth3/.venv requirements.lock

# 2) Application code
COPY server/app ./app
COPY server/pytest.ini ./
COPY server/tests ./tests

# Dev/test deps are kept OUT of the runtime image on purpose. To run the test
# suite inside the image:
#   docker build -f deploy/Dockerfile -t fasth3-api .
#   docker run --rm --entrypoint sh fasth3-api -c \
#     'uv pip sync --python /srv/fasth3/.venv requirements-dev.lock && python -m pytest -q'
COPY server/requirements-dev.txt server/requirements-dev.lock ./

# 3) Make the uv environment the default interpreter
ENV VIRTUAL_ENV=/srv/fasth3/.venv
ENV PATH="/srv/fasth3/.venv/bin:$PATH"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:8000/health', timeout=4)" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
