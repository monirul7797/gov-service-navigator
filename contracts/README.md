# Contracts

Every cross-service API has example JSON in this folder (for example `healthz/response.example.json`). Each implementation's tests assert against these files, so the Python engine, the Rust web service and any future component agree on the exact shape of requests and responses, and a future Rust port of the engine must pass the same tests unchanged.
