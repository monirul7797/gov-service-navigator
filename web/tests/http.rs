//! Integration tests over the public and admin routers (no sockets except the engine fakes).

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::routing::get;
use gsn_web::readiness::{LiveReadiness, Readiness};
use gsn_web::routes::{AppState, admin_router, public_router};
use http_body_util::BodyExt;
use serde_json::Value;
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use tower::ServiceExt;

struct FakeReadiness {
    db: bool,
    engine: bool,
}

#[async_trait]
impl Readiness for FakeReadiness {
    async fn database_ok(&self) -> bool {
        self.db
    }
    async fn engine_ok(&self) -> bool {
        self.engine
    }
}

fn state(db: bool, engine: bool) -> AppState {
    AppState {
        version: "dev".to_string(),
        readiness: Arc::new(FakeReadiness { db, engine }),
    }
}

fn no_static_dir() -> PathBuf {
    PathBuf::from("/nonexistent/gsn-static")
}

async fn get_json(router: axum::Router, uri: &str) -> (StatusCode, Value) {
    let (status, body) = get_text(router, uri).await;
    let value = serde_json::from_str(&body).unwrap_or(Value::Null);
    (status, value)
}

async fn get_text(router: axum::Router, uri: &str) -> (StatusCode, String) {
    let response = router
        .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (status, String::from_utf8_lossy(&bytes).into_owned())
}

fn contract_keys() -> BTreeSet<String> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../contracts/healthz/response.example.json");
    let raw = std::fs::read_to_string(path).expect("contract example must exist");
    let value: Value = serde_json::from_str(&raw).unwrap();
    value.as_object().unwrap().keys().cloned().collect()
}

#[tokio::test]
async fn public_healthz_matches_contract() {
    let router = public_router(state(true, true), &no_static_dir());
    let (status, body) = get_json(router, "/healthz").await;
    assert_eq!(status, StatusCode::OK);
    let keys: BTreeSet<String> = body.as_object().unwrap().keys().cloned().collect();
    assert_eq!(keys, contract_keys());
    assert_eq!(body["status"], "ok");
    assert_eq!(body["service"], "web");
    assert_eq!(body["version"], "dev");
}

#[tokio::test]
async fn healthz_reports_configured_version() {
    let mut app_state = state(true, true);
    app_state.version = "1.2.3".to_string();
    let router = public_router(app_state, &no_static_dir());
    let (_, body) = get_json(router, "/healthz").await;
    assert_eq!(body["version"], "1.2.3");
}

#[tokio::test]
async fn admin_healthz_reports_admin_service() {
    let router = admin_router(state(true, true), &no_static_dir());
    let (status, body) = get_json(router, "/healthz").await;
    assert_eq!(status, StatusCode::OK);
    let keys: BTreeSet<String> = body.as_object().unwrap().keys().cloned().collect();
    assert_eq!(keys, contract_keys());
    assert_eq!(body["status"], "ok");
    assert_eq!(body["service"], "web-admin");
}

#[tokio::test]
async fn readyz_ok_when_all_checks_pass() {
    let router = public_router(state(true, true), &no_static_dir());
    let (status, body) = get_json(router, "/readyz").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body,
        serde_json::json!({"status": "ok", "checks": {"database": "ok", "engine": "ok"}})
    );
}

#[tokio::test]
async fn readyz_degraded_when_engine_down() {
    let router = public_router(state(true, false), &no_static_dir());
    let (status, body) = get_json(router, "/readyz").await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body["status"], "degraded");
    assert_eq!(body["checks"]["engine"], "fail");
    assert_eq!(body["checks"]["database"], "ok");
}

#[tokio::test]
async fn readyz_degraded_when_database_down() {
    let router = public_router(state(false, true), &no_static_dir());
    let (status, body) = get_json(router, "/readyz").await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body["status"], "degraded");
    assert_eq!(body["checks"]["database"], "fail");
    assert_eq!(body["checks"]["engine"], "ok");
}

#[tokio::test]
async fn public_router_has_no_admin_routes() {
    for uri in ["/admin-api/healthz", "/admin-api/x"] {
        let router = public_router(state(true, true), &no_static_dir());
        let (status, _) = get_text(router, uri).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{uri}");
    }
}

#[tokio::test]
async fn admin_router_does_not_serve_readyz() {
    let router = admin_router(state(true, true), &no_static_dir());
    let (status, _) = get_text(router, "/readyz").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn serves_static_index_from_public_dir() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("index.html"), "<p>hi</p>").unwrap();
    let router = public_router(state(true, true), dir.path());
    let (status, body) = get_text(router, "/").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("hi"));
}

#[tokio::test]
async fn serves_static_index_from_admin_dir() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("index.html"), "<p>admin</p>").unwrap();
    let router = admin_router(state(true, true), dir.path());
    let (status, body) = get_text(router, "/").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("admin"));
}

#[tokio::test]
async fn healthz_still_wins_over_a_static_file_of_the_same_name() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("healthz"), "static").unwrap();
    let router = public_router(state(true, true), dir.path());
    let (status, body) = get_json(router, "/healthz").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["status"], "ok");
}

fn unreachable_pool() -> sqlx::PgPool {
    let options = PgConnectOptions::new()
        .host("127.0.0.1")
        .port(1)
        .username("nobody")
        .password("p@ss/w:rd#%?")
        .database("none");
    PgPoolOptions::new()
        .acquire_timeout(Duration::from_secs(2))
        .connect_lazy_with(options)
}

#[tokio::test]
async fn healthz_needs_no_database() {
    let live = LiveReadiness::new(unreachable_pool(), "http://127.0.0.1:1".to_string());
    let app_state = AppState {
        version: "dev".to_string(),
        readiness: Arc::new(live),
    };
    let router = public_router(app_state, &no_static_dir());
    let (status, body) = get_json(router, "/healthz").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["status"], "ok");
}

#[tokio::test]
async fn live_readiness_reports_down_when_nothing_is_reachable() {
    let live = LiveReadiness::new(unreachable_pool(), "http://127.0.0.1:1".to_string());
    assert!(!live.database_ok().await);
    assert!(!live.engine_ok().await);
    let app_state = AppState {
        version: "dev".to_string(),
        readiness: Arc::new(live),
    };
    let (status, body) = get_json(public_router(app_state, &no_static_dir()), "/readyz").await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body["status"], "degraded");
    assert_eq!(body["checks"]["database"], "fail");
    assert_eq!(body["checks"]["engine"], "fail");
}

/// Start a throwaway HTTP server that answers `GET /healthz` with `status`; returns its base URL.
async fn fake_engine(status: StatusCode) -> String {
    let app = axum::Router::new().route("/healthz", get(move || async move { status }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    format!("http://{addr}")
}

#[tokio::test]
async fn engine_ok_only_for_http_200() {
    let ok = LiveReadiness::new(unreachable_pool(), fake_engine(StatusCode::OK).await);
    assert!(ok.engine_ok().await);
    let failing = LiveReadiness::new(
        unreachable_pool(),
        fake_engine(StatusCode::INTERNAL_SERVER_ERROR).await,
    );
    assert!(!failing.engine_ok().await);
    // A trailing slash on the configured URL must not break the probe.
    let slash = LiveReadiness::new(
        unreachable_pool(),
        format!("{}/", fake_engine(StatusCode::OK).await),
    );
    assert!(slash.engine_ok().await);
}
