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

pub struct LiveReadiness {
    pool: PgPool,
    engine_healthz_url: String,
    client: reqwest::Client,
}

impl LiveReadiness {
    pub fn new(pool: PgPool, engine_url: String) -> Self {
        Self {
            pool,
            engine_healthz_url: format!("{}/healthz", engine_url.trim_end_matches('/')),
            client: reqwest::Client::new(),
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
        let request = self
            .client
            .get(&self.engine_healthz_url)
            .timeout(PROBE_TIMEOUT)
            .send();
        match tokio::time::timeout(PROBE_TIMEOUT, request).await {
            Ok(Ok(response)) => response.status() == reqwest::StatusCode::OK,
            _ => false,
        }
    }
}
