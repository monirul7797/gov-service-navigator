// `sqlx::migrate!("../db/migrations")` embeds the migration files into the binary at compile time,
// and cargo does not know those files affect the build. Without this line a new or edited .sql file
// would not trigger a recompile, and `gsn-web migrate` would silently run an old set of migrations.
fn main() {
    println!("cargo:rerun-if-changed=../db/migrations");
}
