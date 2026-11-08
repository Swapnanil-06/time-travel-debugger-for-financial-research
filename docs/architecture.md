# Architecture and trade-offs

## The unit of reproducibility

A run consists of a canonical JSON manifest and five content-addressed artifacts:

| Artifact | Encoding | Purpose |
| --- | --- | --- |
| `data` | Canonical JSON | Ordered, validated observations with ISO dates and normalized decimal strings. |
| `source` | Exact UTF-8 bytes | The captured implementation, including its source-level version. |
| `config` | Canonical JSON | Integer lookback and forecast horizon. |
| `environment` | Canonical JSON | Python version, implementation, platform family, Decimal policy and engine-source digest. |
| `output` | Canonical JSON | Decimal-string forecasts, last observed yield, drift and volatility. |

JSON object keys are sorted, whitespace is removed, UTF-8 is explicit and non-finite JSON numbers are rejected. Row order is meaningful. Equivalent decimal spellings such as `4.20` and `4.200` normalize to the same data value. Source files use LF endings through `.gitattributes`.

The manifest also records an ID, label, UTC timestamp, parent run, model name, observation count, last observation date and provenance. Its own SHA-256 digest is stored separately. Approval binds to that manifest digest; computational identity across differently named runs is established by the five artifact digests.

## Storage and integrity

SQLite runs in WAL mode. Every request uses short-lived connections with foreign-key enforcement and transactions. A capture inserts its artifacts and run record atomically. Repeated artifacts deduplicate by digest. Triggers reject run/blob updates and deletes; the application provides no corresponding endpoints.

Reads verify manifest and artifact bytes. Replay additionally verifies the recorded execution context, checks the captured source against reviewed bundled implementations, and recomputes the output before accepting a match. A damaged output artifact is not accepted simply because a run's metadata says it succeeded.

The hash store is an integrity mechanism, not an authenticity mechanism. An operator with unrestricted database access can remove triggers and recompute checksums. External signatures, authenticated approvers and append-only remote audit storage would be separate production requirements.

## Execution boundary

`engine.execute` accepts only source bytes whose digest matches a bundled model implementation. It never accepts uploaded executable code. The selected source is compiled and run with normalized data and configuration inside an explicit Decimal context. Both implementations are small and depend only on the Python standard library.

This allowlist is not a sandbox. The repository and bundled source are trusted. If a previously captured model is no longer in the reviewed registry, replay fails explicitly rather than executing unknown historical code.

Replay checks the recorded Python patch version and OS family as well as the execution-engine source digest. This is a conservative compatibility check, not a complete machine/container image: it does not fingerprint the interpreter binary, OS patch level or every standard-library file. A production system requiring stronger environmental guarantees should use immutable runtime images and pinned dependencies.

## Comparison semantics

The primary comparison is factual: which captured observations, parameters and artifact hashes differ?

For compatible runs, the workbench executes an ordered sequence:

1. Original source + original data + original configuration.
2. Original source + candidate data + original configuration.
3. Candidate source + candidate data + original configuration.
4. Candidate source + candidate data + candidate configuration.

Differences are measured at the shorter shared forecast horizon. Each contribution is conditional on the preceding substitutions. Their sum recovers the total difference to Decimal precision, but the allocation is order-dependent when effects interact. It is not causal attribution or a Shapley decomposition. If candidate data is too short for an intermediate lookback, the comparison remains visible and the decomposition is reported as unavailable.

The current reviewed engine executes these counterfactuals. Unlike strict replay, comparison is not a claim of reproducing a historical environment.

## Release semantics

Approval requires successful reference replay and records its manifest checksum. A release check requires that approval to remain consistent, successfully replays both reference and candidate, and compares all five artifact fingerprints. A new label is acceptable; altered data, source, configuration, environment or output is not.

The response is a decision record for the local demonstration. There is no production deployment, authenticated approval workflow, scheduling service or external warehouse integration.

## Deliberate implementation choices

- **Standard library over a framework:** the reviewer can run the entire project with Python alone, and the integrity mechanism remains easy to inspect. The HTTP server is intended for loopback development use.
- **SQLite over an external database:** transactional persistence and constraints without setup. Artifact volume and concurrency are modest; this is not a distributed job system.
- **Decimal over binary floats:** explicit precision and stable serialization for a small deterministic example. The browser uses floating point only for formatting and chart placement; replay validation remains server-side.
- **Snapshot copies over live fetches:** every replay is offline. Changes at the data provider cannot silently alter an earlier captured run.
- **A simple model over claimed forecasting sophistication:** a transparent baseline yields a hand-verifiable expected effect. This project evaluates research infrastructure, not investment performance.
- **Native browser APIs over frontend dependencies:** an SVG chart, accessible controls, keyboard-operated tabs and a responsive interface without a package-manager build.

## Cloudflare execution and persistence

`worker.py` handles incoming Worker requests. Static pages and assets use the `ASSETS` binding. API calls are routed by a random browser-workspace cookie to the SQLite-backed `ResearchWorkspace` Durable Object; no global shared research database is exposed to visitors.

`app/cloud_store.py` adapts the synchronous Cloudflare SQLite API to the small connection/cursor interface used by `Store`. Byte artifacts are losslessly transported as base64 text and decoded before hashing. The core capture, comparison, replay and approval methods are inherited from the same `Store` used locally. SQLite triggers still prevent run and artifact mutation.

The Durable Object wraps seeding and dispatch in `storage.transactionSync`. All SQL cursors are fully consumed synchronously, with no awaits inside that transaction. This preserves atomic captures and prevents request interleaving. `app/cloud_api.py` provides transport-independent API dispatch. The existing local HTTP service remains available and retains its local filesystem database.

`scripts/prepare_worker.py` verifies data provenance, prepares a bounded Python module root and bundles the pinned Cloudflare runtime SDK. Wrangler builds and serves the Python entry point; there is no local TCP listener or filesystem database in the cloud runtime. Only source modules required by that entry point are copied. Build output, vendored SDKs and local runtime state are ignored by Git.

The browser cookie is an unguessable bearer capability, not an authenticated identity. Cloud sessions have bounded input/run counts, but account-based access, approval signatures, operational quotas and production scheduling remain outside this demonstration's claims.

## Source map

```text
app/engine.py       Canonicalization, validation, reviewed execution, environment
app/model_v*.py     Two explicit forecasting implementations
app/store.py        Capture, hash store, replay, comparison, approval and release
app/bundle.py       Database-independent bundle verification and replay
app/server.py       Loopback HTTP API and static assets
app/cli.py          Terminal demo, listing, replay, export and verification
app/cloud_store.py  Durable Object SQLite adapter using the same research store
app/cloud_api.py    Transport-independent hosted API dispatch
worker.py           Cloudflare entry point and isolated workspace Durable Object
wrangler.jsonc      Worker, static assets, migration and build configuration
scripts/            Verified bundle preparation and real-runtime test runner
static/            Browser workbench, chart and guided investigation
data/              Original CSV and retrieval/provenance record
tests/             Model, storage, bundle and HTTP contract tests
```
