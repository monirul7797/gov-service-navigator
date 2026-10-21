//! `gsn-web` command line: `serve` (default), `migrate`, `healthcheck`.
//!
//! Logs are one JSON object per line on stdout. Secrets are never logged.

use std::error::Error;
use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use gsn_web::config::{self, Config};
use gsn_web::readiness::{LiveReadiness, internal_http_client};
use gsn_web::routes::{AppState, admin_router, public_router};
use sqlx::postgres::PgPoolOptions;
use tokio::net::TcpListener;
use tokio::sync::watch;
use tracing_subscriber::EnvFilter;

type BoxError = Box<dyn Error>;

fn init_logging() {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(filter)
        .with_writer(std::io::stdout)
        .init();
}

#[tokio::main]
async fn main() -> ExitCode {
    init_logging();
    let command = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "serve".to_string());
    let outcome = match command.as_str() {
        "serve" => serve().await,
        "migrate" => migrate().await,
        "healthcheck" => healthcheck().await,
        other => {
            tracing::error!(
                command = other,
                "unknown command; use serve, migrate or healthcheck"
            );
            return ExitCode::from(2);
        }
    };
    match outcome {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            tracing::error!(command = %command, error = %error, "command failed");
            ExitCode::FAILURE
        }
    }
}

/// Run both listeners until SIGTERM / Ctrl-C. The database pool is lazy, so the service starts
/// and answers `/healthz` even when PostgreSQL is not up yet.
async fn serve() -> Result<(), BoxError> {
    let cfg = Config::from_env()?;
    let pool = PgPoolOptions::new()
        .max_connections(10)
        .acquire_timeout(Duration::from_secs(2))
        .connect_lazy_with(cfg.db.connect_options());
    let state = AppState {
        version: cfg.version.clone(),
        readiness: Arc::new(LiveReadiness::new(pool, cfg.engine_url.clone())),
    };

    let public_listener = TcpListener::bind(cfg.public_bind).await?;
    let admin_listener = TcpListener::bind(cfg.admin_bind).await?;
    tracing::info!(
        public = %cfg.public_bind,
        admin = %cfg.admin_bind,
        version = %cfg.version,
        "gsn-web listening"
    );

    let public_app = public_router(state.clone(), &cfg.public_static_dir);
    let admin_app = admin_router(state, &cfg.admin_static_dir);

    let (stop_tx, stop_rx) = watch::channel(false);
    tokio::spawn(async move {
        shutdown_signal().await;
        tracing::info!("shutdown signal received");
        let _ = stop_tx.send(true);
    });

    let mut public_stop = stop_rx.clone();
    let mut admin_stop = stop_rx;
    let public_server =
        axum::serve(public_listener, public_app).with_graceful_shutdown(async move {
            let _ = public_stop.wait_for(|stop| *stop).await;
        });
    let admin_server = axum::serve(admin_listener, admin_app).with_graceful_shutdown(async move {
        let _ = admin_stop.wait_for(|stop| *stop).await;
    });
    tokio::try_join!(public_server, admin_server)?;
    Ok(())
}

/// Apply `db/migrations/` (compiled into the binary). Run with the `gsn_migrate` credentials.
async fn migrate() -> Result<(), BoxError> {
    let cfg = Config::from_env()?;
    let pool = PgPoolOptions::new()
        .max_connections(1)
        .acquire_timeout(Duration::from_secs(30))
        .connect_with(cfg.db.connect_options())
        .await?;
    sqlx::migrate!("../db/migrations").run(&pool).await?;
    tracing::info!("migrations applied");
    Ok(())
}

/// Container healthcheck: exit 0 iff `GET http://127.0.0.1:<public port>/healthz` returns 200.
async fn healthcheck() -> Result<(), BoxError> {
    let port = config::public_port_from_vars(|name| std::env::var(name).ok())?;
    let url = format!("http://127.0.0.1:{port}/healthz");
    let response = internal_http_client(Duration::from_secs(2))?
        .get(&url)
        .send()
        .await?;
    if response.status() == reqwest::StatusCode::OK {
        Ok(())
    } else {
        Err(format!("healthz returned HTTP {}", response.status()).into())
    }
}

#[cfg(unix)]
async fn shutdown_signal() {
    use tokio::signal::unix::{SignalKind, signal};
    let mut terminate = signal(SignalKind::terminate()).expect("install SIGTERM handler");
    tokio::select! {
        _ = terminate.recv() => {}
        _ = tokio::signal::ctrl_c() => {}
    }
}

#[cfg(not(unix))]
async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
}
