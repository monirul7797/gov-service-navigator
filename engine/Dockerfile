# Engine image: the FastAPI engine app and the worker share one image (`gsn engine`, `gsn worker`).
#   docker build --target runtime ./engine   (what compose runs)
#   docker build --target test    ./engine   (adds the [dev] extra: pytest, httpx2, ...; the
#                                             integration tests are mounted at run time)
#   docker build --target ml      ./engine   (base + CPU torch and sentence-transformers, see below)
#   docker build --target release ./engine   (ml + the engine + git: the one-off `release` tool)
# Every dependency version comes from constraints.txt (and, for the release image, constraints-ml.txt),
# so rebuilds are reproducible.

FROM python:3.12.15-slim-bookworm AS base
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

# The ML packages for the release tool: CPU-only torch (PyTorch CPU index) and sentence-transformers, with
# the rest of their tree, exactly as locked in constraints-ml.txt and installed with --no-deps, so pip
# resolves nothing here. This stage mounts ONLY that file, not the engine sources: its layer is rebuilt only
# when constraints-ml.txt changes, never because engine code did (the layer is about 1.4 GB; unpacked, the
# image grows by about 1.7 GB). The engine comes in the next stage, on top of it.
FROM base AS ml
RUN --mount=type=bind,source=constraints-ml.txt,target=/constraints-ml.txt \
    pip install --no-cache-dir --no-deps --extra-index-url https://download.pytorch.org/whl/cpu \
        -r /constraints-ml.txt

# One-off release tool (`docker compose --profile tools run --rm release release build ...`): the ML stage
# plus the engine package, installed the way the runtime stage does it but under both lock files, plus git,
# which `gsn release` uses to read release content from a commit of the repository mounted at /repo.git.
# `pip check` then proves that the ML tree and the engine's own dependencies fit together. Model files live
# in the /models volume (HF_HOME) and are never fetched at run time (HF_HUB_OFFLINE); only `gsn models
# fetch`, run with HF_HUB_OFFLINE=0, downloads them. Root is used only to install git and the engine and to
# prepare /models; the entrypoint is `gsn`.
FROM ml AS release
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir /models \
 && chown gsn:gsn /models
RUN --mount=type=bind,target=/build,rw \
    pip install --no-cache-dir -c /build/constraints.txt -c /build/constraints-ml.txt /build \
 && pip check
ENV HF_HOME=/models \
    HF_HUB_OFFLINE=1 \
    GIT_CONFIG_NOSYSTEM=1 \
    GSN_REPO_GIT=/repo.git
USER gsn
ENTRYPOINT ["gsn"]
CMD ["--help"]
