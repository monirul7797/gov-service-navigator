//! Service configuration, read from environment variables.
//!
//! The database is configured as separate parts (never a URL) so a password may contain any
//! character (`@ / : # % ?`). Secrets are never printed: `DbConfig`'s `Debug` redacts the password
//! and `ConfigError` never repeats a value.

use std::fmt;
use std::net::SocketAddr;
use std::path::PathBuf;

use sqlx::postgres::PgConnectOptions;

const DEFAULT_PUBLIC_BIND: &str = "0.0.0.0:8080";
const DEFAULT_ADMIN_BIND: &str = "127.0.0.1:8081";

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ConfigError {
    #[error("missing required environment variable {0}")]
    Missing(&'static str),
    #[error("invalid value for {var}: {reason}")]
    Invalid { var: &'static str, reason: String },
}

#[derive(Clone, PartialEq, Eq)]
pub struct DbConfig {
    pub host: String,
    pub port: u16,
    pub name: String,
    pub user: String,
    pub password: String,
}

impl DbConfig {
    /// Connection options built from the separate parts, so no character in the password needs
    /// escaping.
    pub fn connect_options(&self) -> PgConnectOptions {
        PgConnectOptions::new()
            .host(&self.host)
            .port(self.port)
            .database(&self.name)
            .username(&self.user)
            .password(&self.password)
    }
}

impl fmt::Debug for DbConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("DbConfig")
            .field("host", &self.host)
            .field("port", &self.port)
            .field("name", &self.name)
            .field("user", &self.user)
            .field("password", &"<redacted>")
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub public_bind: SocketAddr,
    pub admin_bind: SocketAddr,
    pub db: DbConfig,
    pub engine_url: String,
    pub public_static_dir: PathBuf,
    pub admin_static_dir: PathBuf,
    pub version: String,
}

/// A variable's value, treating an empty string like an unset variable (compose passes `VAR=`
/// for blank entries).
fn non_empty(get: &impl Fn(&str) -> Option<String>, name: &str) -> Option<String> {
    get(name).filter(|v| !v.is_empty())
}

fn with_default(get: &impl Fn(&str) -> Option<String>, name: &str, default: &str) -> String {
    non_empty(get, name).unwrap_or_else(|| default.to_string())
}

fn required(
    get: &impl Fn(&str) -> Option<String>,
    name: &'static str,
) -> Result<String, ConfigError> {
    non_empty(get, name).ok_or(ConfigError::Missing(name))
}

fn parse_bind(
    get: &impl Fn(&str) -> Option<String>,
    var: &'static str,
    default: &str,
) -> Result<SocketAddr, ConfigError> {
    with_default(get, var, default)
        .parse()
        .map_err(|e: std::net::AddrParseError| ConfigError::Invalid {
            var,
            reason: e.to_string(),
        })
}

/// The port the public listener uses; `healthcheck` needs only this, not the database settings.
pub fn public_port_from_vars(get: impl Fn(&str) -> Option<String>) -> Result<u16, ConfigError> {
    Ok(parse_bind(&get, "GSN_PUBLIC_BIND", DEFAULT_PUBLIC_BIND)?.port())
}

impl Config {
    pub fn from_vars(get: impl Fn(&str) -> Option<String>) -> Result<Config, ConfigError> {
        let public_bind = parse_bind(&get, "GSN_PUBLIC_BIND", DEFAULT_PUBLIC_BIND)?;
        let admin_bind = parse_bind(&get, "GSN_ADMIN_BIND", DEFAULT_ADMIN_BIND)?;
        let db_port = with_default(&get, "GSN_DB_PORT", "5432")
            .parse::<u16>()
            .map_err(|e| ConfigError::Invalid {
                var: "GSN_DB_PORT",
                reason: e.to_string(),
            })?;
        let db = DbConfig {
            host: with_default(&get, "GSN_DB_HOST", "postgres"),
            port: db_port,
            name: with_default(&get, "GSN_DB_NAME", "gsn"),
            user: required(&get, "GSN_DB_USER")?,
            password: required(&get, "GSN_DB_PASSWORD")?,
        };
        Ok(Config {
            public_bind,
            admin_bind,
            db,
            engine_url: with_default(&get, "GSN_ENGINE_URL", "http://engine:8000"),
            public_static_dir: with_default(&get, "GSN_PUBLIC_STATIC_DIR", "/app/static/public")
                .into(),
            admin_static_dir: with_default(&get, "GSN_ADMIN_STATIC_DIR", "/app/static/admin")
                .into(),
            version: with_default(&get, "GSN_VERSION", "dev"),
        })
    }

    pub fn from_env() -> Result<Config, ConfigError> {
        Config::from_vars(|name| std::env::var(name).ok())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn vars(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        move |name| map.get(name).cloned()
    }

    #[test]
    fn defaults_applied() {
        let cfg = Config::from_vars(vars(&[
            ("GSN_DB_USER", "gsn_web"),
            ("GSN_DB_PASSWORD", "secret"),
        ]))
        .unwrap();
        assert_eq!(cfg.public_bind, "0.0.0.0:8080".parse().unwrap());
        assert_eq!(cfg.admin_bind, "127.0.0.1:8081".parse().unwrap());
        assert_eq!(cfg.db.host, "postgres");
        assert_eq!(cfg.db.port, 5432);
        assert_eq!(cfg.db.name, "gsn");
        assert_eq!(cfg.db.user, "gsn_web");
        assert_eq!(cfg.engine_url, "http://engine:8000");
        assert_eq!(cfg.public_static_dir, PathBuf::from("/app/static/public"));
        assert_eq!(cfg.admin_static_dir, PathBuf::from("/app/static/admin"));
        assert_eq!(cfg.version, "dev");
    }

    #[test]
    fn explicit_values_override_defaults() {
        let cfg = Config::from_vars(vars(&[
            ("GSN_DB_USER", "u"),
            ("GSN_DB_PASSWORD", "p"),
            ("GSN_PUBLIC_BIND", "127.0.0.1:9000"),
            ("GSN_ADMIN_BIND", "0.0.0.0:9001"),
            ("GSN_DB_HOST", "db.internal"),
            ("GSN_DB_PORT", "6543"),
            ("GSN_DB_NAME", "other"),
            ("GSN_ENGINE_URL", "http://127.0.0.1:1234"),
            ("GSN_PUBLIC_STATIC_DIR", "/tmp/pub"),
            ("GSN_ADMIN_STATIC_DIR", "/tmp/adm"),
            ("GSN_VERSION", "1.2.3"),
        ]))
        .unwrap();
        assert_eq!(cfg.public_bind.port(), 9000);
        assert_eq!(cfg.admin_bind.port(), 9001);
        assert_eq!(cfg.db.host, "db.internal");
        assert_eq!(cfg.db.port, 6543);
        assert_eq!(cfg.db.name, "other");
        assert_eq!(cfg.engine_url, "http://127.0.0.1:1234");
        assert_eq!(cfg.public_static_dir, PathBuf::from("/tmp/pub"));
        assert_eq!(cfg.admin_static_dir, PathBuf::from("/tmp/adm"));
        assert_eq!(cfg.version, "1.2.3");
    }

    #[test]
    fn missing_db_user_is_error() {
        let get = vars(&[("GSN_DB_PASSWORD", "secret")]);
        assert!(matches!(
            Config::from_vars(get),
            Err(ConfigError::Missing(v)) if v == "GSN_DB_USER"
        ));
    }

    #[test]
    fn missing_db_password_is_error() {
        let get = vars(&[("GSN_DB_USER", "gsn_web")]);
        assert!(matches!(
            Config::from_vars(get),
            Err(ConfigError::Missing(v)) if v == "GSN_DB_PASSWORD"
        ));
    }

    #[test]
    fn empty_value_counts_as_unset() {
        let get = vars(&[("GSN_DB_USER", "gsn_web"), ("GSN_DB_PASSWORD", "")]);
        assert_eq!(
            Config::from_vars(get),
            Err(ConfigError::Missing("GSN_DB_PASSWORD"))
        );
        let cfg = Config::from_vars(vars(&[
            ("GSN_DB_USER", "u"),
            ("GSN_DB_PASSWORD", "p"),
            ("GSN_ENGINE_URL", ""),
        ]))
        .unwrap();
        assert_eq!(cfg.engine_url, "http://engine:8000");
    }

    #[test]
    fn invalid_bind_is_error() {
        let get = vars(&[
            ("GSN_DB_USER", "u"),
            ("GSN_DB_PASSWORD", "p"),
            ("GSN_PUBLIC_BIND", "nonsense"),
        ]);
        assert!(matches!(
            Config::from_vars(get),
            Err(ConfigError::Invalid {
                var: "GSN_PUBLIC_BIND",
                ..
            })
        ));
        let get = vars(&[
            ("GSN_DB_USER", "u"),
            ("GSN_DB_PASSWORD", "p"),
            ("GSN_ADMIN_BIND", "127.0.0.1"),
        ]);
        assert!(matches!(
            Config::from_vars(get),
            Err(ConfigError::Invalid {
                var: "GSN_ADMIN_BIND",
                ..
            })
        ));
    }

    #[test]
    fn invalid_db_port_is_error() {
        let get = vars(&[
            ("GSN_DB_USER", "u"),
            ("GSN_DB_PASSWORD", "p"),
            ("GSN_DB_PORT", "70000"),
        ]);
        assert!(matches!(
            Config::from_vars(get),
            Err(ConfigError::Invalid {
                var: "GSN_DB_PORT",
                ..
            })
        ));
    }

    #[test]
    fn password_with_url_special_chars_is_kept() {
        let password = "p@ss/w:rd#%?";
        let cfg = Config::from_vars(vars(&[
            ("GSN_DB_USER", "gsn_web"),
            ("GSN_DB_PASSWORD", password),
            ("GSN_DB_HOST", "db.internal"),
            ("GSN_DB_PORT", "6543"),
        ]))
        .unwrap();
        assert_eq!(cfg.db.password, password);
        let options = cfg.db.connect_options();
        assert_eq!(options.get_host(), "db.internal");
        assert_eq!(options.get_port(), 6543);
        assert_eq!(options.get_username(), "gsn_web");
        assert_eq!(options.get_database(), Some("gsn"));
    }

    #[test]
    fn password_is_never_in_debug_output() {
        let cfg = Config::from_vars(vars(&[
            ("GSN_DB_USER", "gsn_web"),
            ("GSN_DB_PASSWORD", "hunter2-very-secret"),
        ]))
        .unwrap();
        let shown = format!("{cfg:?} {:?}", cfg.db);
        assert!(!shown.contains("hunter2-very-secret"));
        assert!(shown.contains("<redacted>"));
    }

    #[test]
    fn public_port_needs_no_database_settings() {
        assert_eq!(public_port_from_vars(vars(&[])).unwrap(), 8080);
        assert_eq!(
            public_port_from_vars(vars(&[("GSN_PUBLIC_BIND", "0.0.0.0:9090")])).unwrap(),
            9090
        );
        assert!(matches!(
            public_port_from_vars(vars(&[("GSN_PUBLIC_BIND", "x")])),
            Err(ConfigError::Invalid {
                var: "GSN_PUBLIC_BIND",
                ..
            })
        ));
    }
}
