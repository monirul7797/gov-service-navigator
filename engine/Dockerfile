# Engine image: the FastAPI engine app and the worker share one image (`gsn engine`, `gsn worker`).
#   docker build --target runtime ./engine   (what compose runs)
#   docker build --target test    ./engine   (adds the [dev] extra: pytest, httpx2, ...; the
#                                             integration tests are mounted at run time)
# Every dependency version comes from constraints.txt, so rebuilds are reproducible.

FROM python:3.12.10-slim-bookworm AS base
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
