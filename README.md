# DataCoolie Studio

Local web app for inspecting and editing DataCoolie metadata, stitched lineage,
code-discovered inputs, and ETL execution logs.

## Run

```powershell
pip install -e .
datacoolie-studio
```

The launcher starts a local FastAPI server at `http://127.0.0.1:8765`, creates
a SQLite workspace database on first run, and opens the browser.

By default, the SQLite workspace database is stored at:

```powershell
~\.datacoolie\datacoolie-studio\db\studio.db
```

Use `--db` or `DATACOOLIE_STUDIO_DB` to override it.

For a shared or production-style deployment, point Studio at any SQLAlchemy
database URL instead of the local SQLite file:

```powershell
datacoolie-studio --database-url "postgresql+psycopg://user:password@host:5432/datacoolie_studio"
```

`DATACOOLIE_STUDIO_DATABASE_URL` has priority over `DATACOOLIE_STUDIO_DB`.
SQLite is the simplest default for a single local user. Use Postgres when
multiple users or a hosted Studio instance need the same projects,
environments, source settings, drafts, sync jobs, and current source
materializations.

Derived result cache entries are stored separately from the workspace database.
The default local cache is:

```powershell
~\.datacoolie\datacoolie-studio\cache\read-models.sqlite3
```

Override it with a SQLite URL when needed:

```powershell
$env:DATACOOLIE_STUDIO_RESULT_CACHE_URL = "sqlite:///D:/studio-cache/read-models.sqlite3"
```

Redis URLs are not accepted yet. The cache contract is backend-independent,
but the shipped backend remains SQLite.

Local Studio state is organized under:

```powershell
~\.datacoolie\datacoolie-studio\
  db\
  backups\
  cache\
    read-models.sqlite3
    analytics.duckdb
  logs\
```

Useful flags:

```powershell
datacoolie-studio --port 8765
datacoolie-studio --host 127.0.0.1
datacoolie-studio --db .\.scratch\studio.db
datacoolie-studio --no-open
```

Default host is `127.0.0.1` so the app is not exposed on the network by
accident.

## Basic User Flow

1. Create a project.
2. Create an environment such as `dev`, `test`, `prod`, or a custom name.
3. Add one or more metadata files in JSON, YAML, or XLSX format.
4. Add one or more local `etl_logs` paths.
5. Optionally add code artifacts used by metadata Python functions.
6. Use the Overview, Sources, Metadata, Lineage, and Monitoring modules.

The Metadata module opens metadata as spreadsheet-style sheets for
`connections`, `dataflows`, and `schema_hints`. The original metadata file
remains the source of truth. Drafts are stored in SQLite, and saving changes
requires confirmation, validates the document, creates a backup under
`~\.datacoolie\datacoolie-studio\backups`, and then overwrites the source file
in its original JSON, YAML, or XLSX format.

Lineage is stitched for viewing only. For example, metadata source 1 can define
`A -> B` and metadata source 2 can define `B -> C`; Studio shows `A -> B -> C`
without writing a merged metadata file.

Lineage also statically inspects SQL queries and configured Python code
artifacts. Discovered table and path inputs are resolved against metadata
assets when the identity is exact. Ambiguous and unresolved references remain
explicit dependency evidence and are never merged by fuzzy name matching.
Dataflows remain direct asset-to-asset arrows; SQL and Python inputs are shown
as separate dashed dependency relations.

### Code Artifacts

Add code artifacts from the environment Sources page. Supported artifact
types are:

- a local directory;
- a ZIP archive;
- a Python wheel;
- an installed Python distribution in Studio's active environment.

For a `src` layout, set **Module roots** to `src`. Multiple roots use a
comma-separated list. A metadata function such as
`functions.sources.read_orders` is resolved against the indexed module
`functions.sources`.

Validation checks that the artifact is readable and indexable. Refresh creates
a revision-aware module index used by lineage. Studio parses pure-Python source
with LibCST and parses SQL with SQLGlot. It does not import, execute, install,
build, or extract analyzed user code. Compiled-only modules do not produce
lineage and are reported as unsupported or unavailable evidence.

Monitoring reads local DataCoolie `etl_logs` from both:

- `dataflow_run_log` Parquet
- `job_run_log` JSONL

Sources can be refreshed manually from the Sources page. Metadata refresh
replaces the current last-known-good materialization used by Metadata and
Lineage. ETL log
refresh builds a file manifest and loads parsed log rows into the DuckDB
analytics cache under `~\.datacoolie\datacoolie-studio\cache`. Scheduled
refresh can be configured per source; scheduled runs use the same sync job
path as manual refresh. Code artifact refresh builds a safe module index.
Unchanged lineage requests use the disposable result cache instead of reopening
and reparsing code artifacts.

Settings shows workspace, result-cache, and analytics-cache storage separately.
Disposable result and analytics caches can be cleared, pruned, or compacted
without deleting Metadata/Code materializations, drafts, backups, settings, or
sync history. A file-backed SQLite workspace database can be compacted from its
own Maintenance control without clearing core data; this control is hidden for
other database backends. Completed sync history retains the union of the latest
30 days and latest 100 jobs per source; running jobs are never pruned.

The Monitoring tab has nine pages:

- Overview
- Jobs
- Dataflows
- Failures
- Freshness
- Performance
- Volume
- Maintenance
- Diagnostics

## Development

Backend:

```powershell
pip install -e ".[dev]"
uvicorn datacoolie_studio.main:app --reload --port 8765
```

Without editable install, use the source path directly. This is useful for
checking development changes without installing the package. It assumes the
Python dependencies from `pyproject.toml` are already available in the active
environment:

```powershell
$env:PYTHONPATH = "$PWD\src"
uvicorn datacoolie_studio.main:app --reload --port 8765
```

Then run the frontend dev server in another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies API calls to the backend at
`http://127.0.0.1:8765`.

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Build the frontend into the Python package static directory:

```powershell
cd frontend
npm run build
```

Use the repository verification entrypoint for the backend regression suite,
deterministic OpenAPI contract, generated TypeScript contract, frontend tests
and production build, and the high-severity dependency audit:

```powershell
.\scripts\verify.ps1
.\scripts\verify.ps1 -Mode Full
```

`Fast` is the default and runs the architecture, packaged-static/security, and
OpenAPI backend regression tests. `Full` runs the complete backend suite; both
modes run all frontend checks.

## DataCoolie Usecase-Sim Fixtures

Metadata:

```text
D:\GitHub\datacoolie-arch-5\datacoolie-studio\samples\local_use_cases.json
```

ETL logs:

```text
D:\GitHub\datacoolie-arch-5\datacoolie\usecase-sim\logs\etl_logs\analyst
```
