# Web image: the Rust `gsn-web` binary plus the two static SvelteKit apps.
# Build context is the REPOSITORY ROOT (docker compose build), narrowed by the root .dockerignore,
# which keeps secrets (.env, setenv*.bat), data/ and review bundles out of the context.
#
#   /app/gsn-web            entrypoint (default command: serve)
#   /app/static/public      public site   (GSN_PUBLIC_STATIC_DIR)
#   /app/static/admin       admin panel   (GSN_ADMIN_STATIC_DIR)

# --- public frontend -------------------------------------------------------------------------
FROM node:24.22.1-bookworm-slim AS frontend-public
WORKDIR /build/frontend/public
COPY frontend/public/package.json frontend/public/package-lock.json frontend/public/.npmrc ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/public/ ./
RUN npm run build

# --- admin frontend --------------------------------------------------------------------------
FROM node:24.22.1-bookworm-slim AS frontend-admin
WORKDIR /build/frontend/admin
COPY frontend/admin/package.json frontend/admin/package-lock.json frontend/admin/.npmrc ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/admin/ ./
RUN npm run build

# --- Rust binary -----------------------------------------------------------------------------
FROM rust:1.99.1-bookworm AS rust-build
# Use the image's own toolchain (the same version as web/rust-toolchain.toml) instead of letting
# rustup download the clippy/rustfmt components listed there; they are not needed to build.
ENV RUSTUP_TOOLCHAIN=1.99.1
WORKDIR /build
# sqlx::migrate!("../db/migrations") embeds the migrations at compile time, so db/migrations is
# needed next to web/. contracts/ is only read by tests and is not copied.
COPY db/migrations/ db/migrations/
COPY web/ web/
WORKDIR /build/web
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/web/target \
    cargo build --release --locked \
 && install -D target/release/gsn-web /out/gsn-web

# --- runtime ---------------------------------------------------------------------------------
# distroless has no shell or package manager; the "nonroot" variant runs as uid 65532. The tag is
# pinned by digest because distroless publishes no version tags.
FROM gcr.io/distroless/cc-debian12:nonroot@sha256:9dac0a79194e45a7da0158a9c6da57b217585af0786db3845d1f0ec1a0dd182f AS runtime
WORKDIR /app
COPY --from=rust-build /out/gsn-web /app/gsn-web
COPY --from=frontend-public /build/frontend/public/build/ /app/static/public/
COPY --from=frontend-admin /build/frontend/admin/build/ /app/static/admin/
USER nonroot:nonroot
EXPOSE 8080 8081
ENTRYPOINT ["/app/gsn-web"]
CMD ["serve"]
