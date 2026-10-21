# Studio benchmark tools

These scripts are developer-run diagnostics, not published `datacoolie-studio` commands.
Run them from the Studio checkout with the repository-root virtual environment. The scripts
resolve the package from the checkout; do not use a component-local `.venv` or an implicit
system Python.

For a disposable local v6 analytics-upgrade smoke receipt:

```powershell
$python = (Resolve-Path ..\.venv\Scripts\python.exe).Path
& $python scripts/benchmarks/analytics_upgrade.py
```

The script creates a temporary SQLite workspace and local log roots, then prints JSON containing
the source/file/row scale, phase timings, candidate/live sizes, and finalize count. It is a
boundary-freeze diagnostic, not the representative-volume release benchmark required by the
coordinated contract-release plan.

```powershell
$python = (Resolve-Path ..\.venv\Scripts\python.exe).Path
& $python scripts/benchmarks/databricks_storage.py `
  --directory-uri "dbfs:/Volumes/catalog/schema/volume/logs" `
  --file-uri "dbfs:/Volumes/catalog/schema/volume/logs/sample.jsonl" `
  --runs 5

& $python scripts/benchmarks/databricks_log_sync.py `
  --source-id 42 `
  --runs 3 `
  --data-dir "D:\scratch\datacoolie-studio-benchmark" `
  --allow-write
```

`databricks_storage.py` measures list, stat, and read latency through the Databricks SDK and
does not write to the remote storage. It requires Databricks authentication through the SDK's
ambient configuration or `--profile`.

`databricks_log_sync.py` invokes a real refresh for an existing DBFS log source and writes only
to the explicitly supplied local Studio data directory. `--allow-write` is required as an
acknowledgement. Use a disposable or explicitly approved workspace when running it. All tools
print JSON and accept `--help` without contacting a Databricks workspace.

The deterministic pytest default excludes `live` and `benchmark` tests. Include those suites
explicitly when their capability/configuration is available; see the Studio development runbook.

For native-to-native batch ingestion qualification, first preserve a source snapshot
containing the current native schema18 implementation. Use the strict comparison
mode; the legacy mode retains the separate schema17-to-native representation probe.

```powershell
& ../.venv/Scripts/python.exe scripts/benchmarks/native_ingestion.py --comparison native `
  --source src --baseline-source <native-source-snapshot> `
  --fixtures tests/fixtures/datacoolie/logging/v6 --rows 1024 --files 16 --threads 1 `
  --repeats 5 --receipt <isolated-receipt.json>
```

Run matching `--threads 1` and `--threads 4` profiles with `--files 1`, `16`, `64`
and `256`. Both implementations use the existing file/byte batch limits and
512MiB per DuckDB connection. `--case small` refreshes20 records alongside5000
retained rows; `duplicates` retains equal physical observations; `mixed` interleaves
job/dataflow files; `normalized` includes BOM and excluded records. Use1000 rows
for the duplicate-pair profile. Full-schema135000-record provenance/resource
qualification lives in `test_native_loading.py` and uses1/4 threads.

The receipt includes one excluded warmup and at least five refresh samples,
stage/publication timings, reader/base-write/schema SQL counters, exact base
payload/provenance digests, six real Monitoring read-model parity/latency checks,
source/input fingerprints, process peak RSS and sampled transient/spill bytes.
Canonical fixture URIs and revisions are deterministic across processes;
runtime ingestion timestamps are excluded from the base digest. Read-model
comparison preserves producer JSON/string values and excludes only generated
runtime metadata. Both normal and admitted-byte temporary files live below the
isolated measurement root. Disk sampling is at20ms intervals and may miss shorter
peaks. Results qualify these synthetic local profiles, not arbitrary cloud workloads.

If a matched query gate fails, retain that receipt and rerun with
`--reverse-order` to detect process-order/environment effects. Do not relax the
threshold or omit a failed profile from qualification.

Native comparison subprocesses fix `PYTHONHASHSEED=0` for both implementations.
Query profiling can retain isolated child databases with `--cache-output`; run
paired alternating queries with one consumer when separate-process timing is
unstable. Keep the original failed receipt and document the additional control.

## Monitoring registry cold-load diagnostics

`registry_queries.py` qualifies the existing Monitoring registry read model for
the cold-load plan. It loads the captured Monitoring source under
`../.scratch/registry-cold-load/source-baseline/` while using the current
analytics schema and connection owner. Live mode opens the analytics DuckDB
read-only, begins a transaction before capturing the materialization token, and
rolls the transaction back before releasing each connection. It writes only
bounded JSON receipts under `../.scratch/registry-cold-load/budget/`; the live
cache phase uses `sqlite://` in memory, so live rows are not persisted by the
diagnostic.

`--source-root` defaults to the current checkout source and also accepts a
captured checkout or package root. Pass `--baseline-root` to overlay the
captured Monitoring package used for qualification; omitting it runs the
current source. Each receipt records both roots and the captured manifest hash
when one is supplied.

Use the repository virtual environment from this directory:

```powershell
$python = (Resolve-Path ..\.venv\Scripts\python.exe).Path
$baseline = (Resolve-Path ..\.scratch\registry-cold-load\source-baseline\datacoolie_studio).Path

# T0: startup/setup, fresh uncached reads, and the real result-cache build/hit
& $python scripts/benchmarks/registry_queries.py `
  --mode t0 --dataset live --thread-profile auto --repeats 10 `
  --workloads drawer-all,table-freshness,table-performance,table-volume `
  --baseline-root $baseline `
  --receipt ..\.scratch\registry-cold-load\budget\t0-live-auto.json

# T1: one selected real workload, 20 requests per 1/2/4 concurrency cell
& $python scripts/benchmarks/registry_queries.py `
  --mode t1 --dataset live --workloads drawer-all `
  --thread-profiles 1,2,4,auto --concurrencies 1,2,4 --requests 20 `
  --baseline-root $baseline `
  --receipt ..\.scratch\registry-cold-load\budget\t1-live-drawer.json
```

The drawer workload is the full-model `all` facet with a real entity target.
The three table workloads are `freshness`, `performance`, and `volume`, each
with its own canonical sort. Each request uses an independent configured
connection; the worker never changes `threads` on a shared connection. The
receipt records the fixed anchor, materialization token, source IDs, raw row and
entity counts, DuckDB settings, request/queue p50 and p95, throughput, process
RSS/CPU, sampled DuckDB spill bytes, output digests, and direct output equality.
Equality keeps Decimal values exact and compares binary floats with absolute and
relative tolerance `1e-12`; a failed request or generation mismatch is retained
in the receipt.

Build isolated larger populations without importing live records:

```powershell
& $python scripts/benchmarks/registry_queries.py `
  --mode prepare-synthetic --dataset synthetic --synthetic-rows 10000 `
  --baseline-root $baseline `
  --receipt ..\.scratch\registry-cold-load\budget\synthetic-10000.json

& $python scripts/benchmarks/registry_queries.py `
  --mode prepare-synthetic --dataset synthetic --synthetic-rows 100000 `
  --baseline-root $baseline `
  --receipt ..\.scratch\registry-cold-load\budget\synthetic-100000.json

& $python scripts/benchmarks/registry_queries.py `
  --mode t1 --dataset synthetic --synthetic-rows 10000 `
  --workloads drawer-all,table-freshness `
  --thread-profiles 1,2,4,auto --concurrencies 1,2,4 --requests 20 `
  --baseline-root $baseline `
  --receipt ..\.scratch\registry-cold-load\budget\t1-synthetic-10000.json
```

Each synthetic canonical run has duplicate observations from two sources.
Serving-fact build time uses DuckDB's default construction budget; it records
setup and does not qualify an ingestion thread override. Live source scope is
the retained Dataflow-table sources, rather than the API environment admission
lookup; confirm that scope separately for route claims. Synthetic DuckDB files
contain only deterministic generated values.
Run 100,000 rows as a separate budgeted cell because it can be CPU intensive.

T1 output suggests a reversible explicit deployment override only when the
candidate clears the measured auto baseline by at least 10% across the selected
cells. The tool never changes the auto default, mutates a running service,
restarts a service, or toggles threads per request. If a candidate regresses a
larger or maintenance workload, retain auto and record the override as
scope-limited. These receipts qualify the measured local profiles; they do not
establish a production p95 or an application startup SLA.

### Complete-source registry route measurements

Use `--mode route` when qualifying reader, hydration or route changes. Unlike
the Monitoring overlay, `--source-root` selects the entire captured backend.
Run baseline and candidate in separate processes; do not pass `--baseline-root`.

```powershell
& $python scripts/benchmarks/registry_queries.py --mode route --thread-profile 4 `
  --source-root ../.scratch/registry-drawer-performance-20261016/baseline/src `
  --environment-id 1 --section overview --requests 30 --concurrency 1 `
  --as-of 2026-10-16T10:40:00Z --receipt ../.scratch/registry-drawer-performance-20261016/routes-baseline.json

& $python scripts/benchmarks/registry_queries.py --mode route --thread-profile 4 `
  --source-root src --environment-id 1 --section overview --requests 30 --concurrency 1 `
  --as-of 2026-10-16T10:40:00Z --receipt ../.scratch/registry-drawer-performance-20261016/routes-candidate.json
```

Each scope uses a new second in `asOf`, followed by an exact-scope repeat.
Concurrency 2/4 sends duplicate requests together and exercises cache coordination.
Candidate `Server-Timing` reports the actual hit/miss branch for each request.
Receipts include full module import paths, response status/bytes/digests and
sampled RSS/spill; they contain no response records. Compare returned generations
and retain any digest mismatch. Existing parallel AVG rounding can vary, so a
digest mismatch needs field-level investigation rather than an assumed tolerance.

This mode uses the real FastAPI routes, public reader, private in-memory result
cache and HTTP serialization through ASGI. It skips application lifespan and
TCP/network costs and writes no source/analytics data. The first request is a
fresh-process result miss, not OS disk-cold. Synthetic read-model databases are
not accepted as valid publications by this mode. Use live HTTP/browser receipts
separately for user-facing latency and native public-reader diagnostics for
publication/lifecycle/scale qualification.

Candidate stage headers are nested: `overview_sql_build` is within `registry_sql`,
which is within `cache_service`; `route` includes these stages and coordinator
wait. Do not add their durations. `route` ends before response serialization,
which is included in ASGI wall time. Route HTTP errors and analytics rebuild
errors retain diagnostics; request validation before route execution and
unhandled 500 responses may have no header. A failed cache producer is a miss;
an error before cache access is bypass.
