# DataCoolie Studio

Local web app for inspecting and editing DataCoolie metadata, stitched lineage,
code-discovered inputs, and ETL execution logs.

## Install and run

Requires Python 3.11 or later.

```powershell
pip install datacoolie-studio
datacoolie-studio
```

The launcher starts a local FastAPI server at `http://127.0.0.1:8765`, creates
a SQLite workspace database on first run, and opens the browser.

Install only the cloud providers you use, or install the complete cloud set:

```powershell
pip install "datacoolie-studio[s3]"
pip install "datacoolie-studio[minio]"
pip install "datacoolie-studio[adls]"
pip install "datacoolie-studio[onelake]"
pip install "datacoolie-studio[gcs]"
pip install "datacoolie-studio[dbfs]"
pip install "datacoolie-studio[cloud]"
```

## Run from source

```powershell
pip install -e .
datacoolie-studio
```

Install only the cloud providers you use, or install the complete cloud set:

```powershell
pip install -e ".[s3]"
pip install -e ".[minio]"
pip install -e ".[adls]"
pip install -e ".[onelake]"
pip install -e ".[gcs]"
pip install -e ".[dbfs]"
pip install -e ".[cloud]"
```

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
    source-materializations\
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
4. Add one or more local or cloud `etl_logs` locations.
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

- a local or cloud directory/prefix;
- a local or cloud Python `.py` file;
- a local or cloud ZIP archive;
- a local or cloud Python wheel;
- an installed Python distribution in Studio's active environment.

Module names are discovered from Python package markers. A directory containing
`__init__.py` is a package, while a `.py` file outside a package is indexed by
its filename. For example, selecting either a project containing
`functions/__init__.py` or the `functions` directory itself indexes
`functions/sources.py` as `functions.sources`. A `src` layout is detected
without including `src` in the module name when the packages beneath it contain
`__init__.py`.

**Module roots** can optionally restrict indexing to roots such as `src`, and
**Module prefix override** supports layouts that cannot be inferred
automatically. Multiple roots use a comma-separated list. A metadata function
such as `functions.sources.read_orders` is resolved against the indexed module
`functions.sources`.

Validation checks that the artifact is readable and indexable. Refresh creates
a revision-addressed local snapshot and module index used by lineage. Cloud
snapshots are disposable and rebuilt when missing or stale. Studio parses pure-Python source
with LibCST and parses SQL with SQLGlot. It does not import, execute, install,
build, or extract analyzed user code. Compiled-only modules do not produce
lineage and are reported as unsupported or unavailable evidence.

Monitoring reads local or cloud DataCoolie `etl_logs` from both:

- `dataflow_run_log` Parquet
- `job_run_log` JSONL

Sources can be refreshed manually from the Sources page. Metadata refresh
replaces the current last-known-good materialization used by Metadata and
Lineage. ETL log
refresh builds a file manifest and loads parsed log rows into the DuckDB
analytics cache under `~\.datacoolie\datacoolie-studio\cache`. Scheduled
refresh can be configured per source; scheduled runs use the same sync job
path as manual refresh. Scheduled log refreshes do not overlap for the same
source: when another sync job is running, the due tick is skipped without
advancing the schedule timestamp so a later tick can retry. Code artifact
refresh builds a safe module index.

Studio observes cloud source changes in the background. The global **Cloud
source change check** setting supports Fixed and Adaptive modes. Adaptive mode
defaults to 30 seconds after a change, 60 seconds after the first unchanged
observation, and 5 minutes while the source remains unchanged. The active and
idle intervals are configurable. A detected change or successful manual
refresh returns the cloud source to the active interval. Source observations
use durable per-source due state and an expiring lease, so concurrent scheduler
processes do not check the same source at the same time.

Local sources use the backend filesystem directly and are not scheduled by the
cloud observation interval. Studio observes enabled Local Metadata, Code, and
Log sources when the Sources page opens or returns to the foreground. Cached
Sources content remains visible during this check. Metadata and Code reuse
revision-based materialization, while Logs reuse the saved partition planner
and update only their pending-change state.

Sources, environment header, and freshness API reads use only persisted
observation state. Opening Sources performs a separate Local-only observation;
it never lists or stats cloud storage. Running sync jobs are checked from the
browser every 30 seconds, while idle Sources polling follows the next persisted
cloud due time and pauses when the browser tab is hidden. Log observation only
records whether files are pending. Log ingestion still occurs exclusively
through manual refresh or the source's configured schedule.

Cloud directory observations invalidate provider listing caches before
checking externally mutable prefixes. Test connection uses one bounded
directory probe. Remote Code directory snapshots reuse unchanged cached files
and download only added or changed Python files before atomically publishing a
new revision.

On the first successful log refresh, Studio learns the partition layout of
each Dataflow, Job, and System log stream independently. Later incremental
refreshes render only the exact year, month, or day partitions between the
saved scan watermark and the current UTC partition; they do not rediscover the
directory tree. An explicit lookback renders only the requested inclusive
partition range and combines it with the normal incremental range without
moving the incremental checkpoint backward. Late files below the saved
watermark therefore require an explicit lookback.

The learned layout and per-file manifest are durable workspace state. Clearing
the disposable DuckDB analytics cache keeps them and rebuilds parsed Dataflow
and Job rows from the saved manifest paths without tree discovery. Changing a
source URI, log-root configuration, storage provider, or provider-specific
storage options resets this state; rotating credentials does not. System log
sync uses the same partition planner but stores only file metadata. System
file contents are read on demand when a user opens matching log details.

Unchanged lineage requests use the disposable result cache instead of reopening
and reparsing code artifacts.

Settings shows workspace, result-cache, and analytics-cache storage separately.
Clearing all disposable caches also removes downloaded source snapshots; they
are rebuilt from configured sources on demand. It does not delete source
objects, workspace materialization records, drafts, backups, settings,
credential profiles, or sync history. A file-backed SQLite workspace database can be compacted from its
own Maintenance control without clearing core data; this control is hidden for
other database backends. Completed sync history retains the union of the latest
30 days and latest 100 jobs per source; running jobs are never pruned.

### Cloud Sources And Credentials

Metadata, Logs, and Code sources support Local, Amazon S3, MinIO, Azure Data
Lake Storage, Microsoft OneLake, Google Cloud Storage, and Databricks
DBFS/Unity Catalog Volumes.
Both Project path scans and Manual paths use the same provider, authentication,
URI, and provider-specific configuration. Use **Test connection** before
saving.

Supported URI forms include:

- `s3://bucket/prefix` for S3 and MinIO;
- `abfs://container@account/path` or `abfss://...` for ADLS;
- `abfss://workspace@onelake.dfs.fabric.microsoft.com/lake.Lakehouse/Files/path`
  for a Microsoft OneLake Lakehouse Files location;
- `gs://bucket/prefix` for GCS;
- `dbfs:/Volumes/catalog/schema/volume/prefix` for a recommended Databricks
  Unity Catalog Volume;
- `dbfs:/path` for a legacy DBFS root or mount path;
- a normal folder or file path for Local.

Authentication can use the provider's ambient/default credential chain,
anonymous access where supported, or a reusable Credential Profile created in
Settings. Credential Profiles support AWS shared profiles and access keys,
MinIO access keys, ADLS service principals/SAS/account keys, GCS service
account JSON, OneLake service principals, and Databricks configuration
profiles, personal access tokens, or OAuth M2M service principals. Databricks
ambient authentication follows the SDK unified authentication chain.

Secret fields are write-only and stored in the operating-system credential
store. The workspace database contains only non-secret configuration and an
opaque reference. Therefore, copying or backing up `studio.db` does **not**
back up cloud secrets; recreate or rotate Credential Profiles on the restored
machine. Credential-management and stored-profile interactive operations are
accepted only from a direct loopback client.

Metadata reads and confirmed editor saves work against S3, MinIO, ADLS, and
GCS objects. Saves use provider revision preconditions to reject concurrent
changes. Local retains file backups, while cloud rollback creates a new
conditional object revision from the selected backup. Logs and Code are
read-only source integrations: Studio never writes log or code objects back to
cloud storage. OneLake and Databricks DBFS Metadata are read-only in this
release: OneLake write-back remains gated on a live conditional-write check,
and the Databricks file APIs do not expose the atomic conditional replace
required by Studio's conflict-safety contract.

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

## Release to PyPI

Publishing uses GitHub OIDC Trusted Publishing; no PyPI token is stored in this
repository. Before the first release, a PyPI owner must create a pending
Trusted Publisher for the `datacoolie-studio` project with:

- owner: `datacoolie`
- repository: `datacoolie-studio`
- workflow: `publish-pypi.yml`
- environment: `pypi`

Create the `pypi` GitHub environment and protect it with the desired release
reviewers. Then update the project version, merge it to `main`, and push a
matching tag:

```powershell
git tag -a v0.1.1 -m "Release v0.1.1"
git push origin v0.1.1
```

The tag triggers CI, creates GitHub Release notes, and publishes the checked
wheel and source distribution to PyPI. Publication stops when the tag does not
match the version in `pyproject.toml`.

## DataCoolie Usecase-Sim Fixtures

Metadata:

```text
D:\GitHub\datacoolie-arch-5\datacoolie-studio\samples\local_use_cases.json
```

ETL logs:

```text
D:\GitHub\datacoolie-arch-5\datacoolie\usecase-sim\logs\etl_logs\analyst
```
