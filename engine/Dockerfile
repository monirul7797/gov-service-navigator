# Engine image: the FastAPI engine app and the worker share one image (`gsn engine`, `gsn worker`).
#   docker build --target runtime ./engine   (what compose runs)
#   docker build --target test    ./engine   (adds the [dev] extra: pytest, httpx2, ...; the
#                                             integration tests are mounted at run time)
#   docker build --target release ./engine   (runtime + git + CPU torch and sentence-transformers: the
#                                             one-off `release` tool, see below)
# Every dependency version comes from constraints.txt (and, for the release image, constraints-ml.txt),
# so rebuilds are reproducible.

FROM python:3.12.14-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore
RUN groupadd --gid 10001 gsn \
 && useradd --uid 10001 --gid gsn --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin gsn
WORKDIR /app

FROM base AS runtime
# The build context is bind-mounted (read-write, changes are thrown away) so no source files stay
# in the image: only the installed package does.
RUN --mount=type=bind,target=/build,rw \
    pip install --no-cache-dir -c /build/constraints.txt /build
USER gsn
EXPOSE 8000
CMD ["gsn", "engine", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS test
RUN --mount=type=bind,target=/build,rw \
    pip install --no-cache-dir -c /build/constraints.txt "/build[dev]"
USER gsn
CMD ["python", "-m", "pytest", "-q"]

# One-off release tool (`docker compose --profile tools run --rm release release build ...`): the runtime
# image plus git, which `gsn release` uses to read release content from a commit of the repository
# mounted at /repo.git, plus the ML packages that embed text with BAAI/bge-m3 (CPU-only torch from the
# PyTorch CPU index, sentence-transformers). The ML tree is locked in constraints-ml.txt and installed
# with --no-deps, so pip resolves nothing here; `pip check` then proves the pieces fit together. Model
# files live in the /models volume (HF_HOME) and are never fetched at run time (HF_HUB_OFFLINE); only
# `gsn models fetch`, run with HF_HUB_OFFLINE=0, downloads them. Root is used only to install git and the
# ML packages and to prepare /models; the entrypoint is `gsn`.
FROM runtime AS release
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir /models \
 && chown gsn:gsn /models
RUN --mount=type=bind,target=/build,rw \
    pip install --no-cache-dir --no-deps --extra-index-url https://download.pytorch.org/whl/cpu \
        -r /build/constraints-ml.txt \
 && pip check
ENV HF_HOME=/models \
    HF_HUB_OFFLINE=1 \
    GIT_CONFIG_NOSYSTEM=1 \
    GSN_REPO_GIT=/repo.git
USER gsn
ENTRYPOINT ["gsn"]
CMD ["--help"]
