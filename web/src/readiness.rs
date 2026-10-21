//! Dependency checks behind `/readyz`.
//!
//! `/healthz` ("the process is up") never touches these; only `/readyz` does, so the service stays
//! alive while the database or the engine is slow to start.

use std::time::Duration;

use async_trait::async_trait;
use sqlx::PgPool;

/// How long a single dependency probe may take.
const PROBE_TIMEOUT: Duration = Duration::from_secs(2);

#[async_trait]
pub trait Readiness: Send + Sync {
    async fn database_ok(&self) -> bool;
    async fn engine_ok(&self) -> bool;
}

/// HTTP client for calls inside our own network (the engine, loopback): it ignores proxy
/// settings and never follows redirects, so a probe can only be answered by the host it targets.
/// Built without TLS: the web service makes no https calls in M0 (add a TLS feature to reqwest
/// when one is introduced).
pub fn internal_http_client(timeout: Duration) -> reqwest::Result<reqwest::Client> {
    reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(timeout)
        .build()
}

pub struct LiveReadiness {
    pool: PgPool,
    engine_healthz_url: String,
    /// `None` only if the client could not be built; the engine probe then reports "fail"
    /// instead of the service refusing to start.
    client: Option<reqwest::Client>,
}

impl LiveReadiness {
    pub fn new(pool: PgPool, engine_url: String) -> Self {
        let client = match internal_http_client(PROBE_TIMEOUT) {
            Ok(client) => Some(client),
            Err(error) => {
                tracing::error!(%error, "cannot build the engine HTTP client; engine checks will fail");
                None
            }
        };
        Self {
            pool,
            engine_healthz_url: format!("{}/healthz", engine_url.trim_end_matches('/')),
            client,
        }
    }
}

#[async_trait]
impl Readiness for LiveReadiness {
    /// `SELECT 1` on a pooled connection.
    async fn database_ok(&self) -> bool {
        let probe = sqlx::query("SELECT 1").execute(&self.pool);
        matches!(tokio::time::timeout(PROBE_TIMEOUT, probe).await, Ok(Ok(_)))
    }

    /// `GET {engine_url}/healthz`: ok only for HTTP 200 within the timeout.
    async fn engine_ok(&self) -> bool {
        let Some(client) = &self.client else {
            return false;
        };
        let request = client.get(&self.engine_healthz_url).send();
        match tokio::time::timeout(PROBE_TIMEOUT, request).await {
            Ok(Ok(response)) => response.status() == reqwest::StatusCode::OK,
            _ => false,
        }
    }
}
