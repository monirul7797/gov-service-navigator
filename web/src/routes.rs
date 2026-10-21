//! The two routers: public (internet-facing) and admin (reachable only over the private network).
//!
//! They are separate `Router`s on separate listeners so an admin route can never be reached
//! through the public port: the public router contains no admin routes at all.

use std::path::Path;
use std::sync::Arc;

use axum::Router;
use axum::extract::{Request, State};
use axum::http::StatusCode;
use axum::response::Json;
use axum::routing::{MethodRouter, get};
use serde_json::{Value, json};
use tower_http::services::ServeDir;
use tower_http::trace::TraceLayer;

use crate::readiness::Readiness;

#[derive(Clone)]
pub struct AppState {
    pub version: String,
    pub readiness: Arc<dyn Readiness>,
}

/// `GET /healthz` for the named service: the shape in `contracts/healthz/response.example.json`.
fn healthz(service: &'static str) -> MethodRouter<AppState> {
    get(move |State(state): State<AppState>| async move {
        Json(json!({
            "status": "ok",
            "service": service,
            "version": state.version,
        }))
    })
}

/// `GET /readyz`: 200 when the database and the engine both answer, otherwise 503 "degraded".
async fn readyz(State(state): State<AppState>) -> (StatusCode, Json<Value>) {
    let (database, engine) =
        tokio::join!(state.readiness.database_ok(), state.readiness.engine_ok());
    let label = |ok: bool| if ok { "ok" } else { "fail" };
    let all_ok = database && engine;
    let status = if all_ok {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    let body = json!({
        "status": if all_ok { "ok" } else { "degraded" },
        "checks": {"database": label(database), "engine": label(engine)},
    });
    (status, Json(body))
}

/// A request span with the method and path only: query strings can carry personal data and must
/// not reach the logs.
fn request_span(request: &Request) -> tracing::Span {
    tracing::info_span!(
        "request",
        method = %request.method(),
        path = %request.uri().path(),
    )
}

/// Public router: `/healthz`, `/readyz` and the static frontend. No admin routes.
pub fn public_router(state: AppState, static_dir: &Path) -> Router {
    Router::new()
        .route("/healthz", healthz("web"))
        .route("/readyz", get(readyz))
        .fallback_service(ServeDir::new(static_dir))
        .layer(TraceLayer::new_for_http().make_span_with(request_span))
        .with_state(state)
}

/// Admin router: `/healthz` and the static admin frontend.
pub fn admin_router(state: AppState, static_dir: &Path) -> Router {
    Router::new()
        .route("/healthz", healthz("web-admin"))
        .fallback_service(ServeDir::new(static_dir))
        .layer(TraceLayer::new_for_http().make_span_with(request_span))
        .with_state(state)
}
