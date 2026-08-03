# DataCoolie Studio

DataCoolie Studio is a local web app for exploring DataCoolie projects. Use it to manage sources, edit metadata, inspect lineage, and monitor extract, transform, and load (ETL) runs.

## What you can do

- Organize sources by Project and Environment
- Read and edit metadata in `JSON`, `YAML`, or `XLSX` format
- Inspect lineage from metadata, SQL queries, and Python code
- Monitor Dataflow, Job, and System logs
- Connect to Local, S3, MinIO, ADLS, OneLake, GCS, and Databricks storage
- Store cloud credentials in the operating system credential store

Studio keeps source files as the source of truth. Metadata saves validate the document and create a backup before replacing the original file. Lineage combines evidence for display without creating a merged metadata file.

## Screenshots

The screenshots below show the main Studio workflow using a populated local DataCoolie environment.

### Projects and Environment

Projects provides a single workspace view for project readiness, environment navigation, and source coverage.

![Projects workspace](docs/screenshots/home-projects.png)

Environment Overview brings Metadata, Lineage, Monitoring, freshness, and next actions together in one screen.

![Environment Overview](docs/screenshots/overview.png)

### Metadata, Assets, Lineage, and Sources

Metadata presents connections, dataflows, schema hints, and ordered source-defined transform configuration in an editable workspace.

![Metadata workspace](docs/screenshots/metadata.png)

Assets provides an inventory of discovered assets and references, including resolution and usage context.

![Assets inventory](docs/screenshots/assets.png)

Lineage connects metadata, SQL, and Python evidence into an interactive graph with filters and run-status context.

![Lineage graph](docs/screenshots/lineage.png)

Sources shows Local and cloud bindings, readable/cache status, scheduled Log refresh, and one-click path copying.

![Sources and storage](docs/screenshots/sources.png)

### Monitoring

Monitoring is split into nine focused pages so operational questions can be answered without leaving the Environment.

<details>
<summary>Open all 9 Monitoring pages</summary>

<table width="100%">
<tr>
<td valign="top" width="50%"><strong>Overview</strong><br><sub>Health KPIs, trends, runtime context, and attention signals.</sub><br><img src="docs/screenshots/monitoring-overview.png" alt="Monitoring Overview" width="100%"></td>
<td valign="top" width="50%"><strong>Jobs</strong><br><sub>Job status, duration, runtime context, and drill-in evidence.</sub><br><img src="docs/screenshots/monitoring-jobs.png" alt="Monitoring Jobs" width="100%"></td>
</tr>
<tr>
<td valign="top"><strong>Dataflows</strong><br><sub>Dataflow filtering, execution status, timings, and source/destination context.</sub><br><img src="docs/screenshots/monitoring-dataflows.png" alt="Monitoring Dataflows" width="100%"></td>
<td valign="top"><strong>Failures</strong><br><sub>Failure categories, repeated failures, and investigation entry points.</sub><br><img src="docs/screenshots/monitoring-failures.png" alt="Monitoring Failures" width="100%"></td>
</tr>
<tr>
<td valign="top"><strong>Freshness</strong><br><sub>Source freshness, event time, watermarks, and stale-data signals.</sub><br><img src="docs/screenshots/monitoring-freshness.png" alt="Monitoring Freshness" width="100%"></td>
<td valign="top"><strong>Performance</strong><br><sub>Duration percentiles, phase contribution, pressure, and candidates.</sub><br><img src="docs/screenshots/monitoring-performance.png" alt="Monitoring Performance" width="100%"></td>
</tr>
<tr>
<td valign="top"><strong>Volume</strong><br><sub>Rows, bytes, files, workload trends, and file-churn candidates.</sub><br><img src="docs/screenshots/monitoring-volume.png" alt="Monitoring Volume" width="100%"></td>
<td valign="top"><strong>Maintenance</strong><br><sub>Maintenance operations, destination impact, and performance signals.</sub><br><img src="docs/screenshots/monitoring-maintenance.png" alt="Monitoring Maintenance" width="100%"></td>
</tr>
<tr>
<td valign="top"><strong>Diagnostics</strong><br><sub>Bounded diagnostic aggregates and investigation evidence.</sub><br><img src="docs/screenshots/monitoring-diagnostics.png" alt="Monitoring Diagnostics" width="100%"></td>
<td valign="top"></td>
</tr>
</table>

</details>

## Install and run

DataCoolie Studio requires Python 3.11 or later.

```powershell
pip install datacoolie-studio
datacoolie-studio
```

The launcher starts Studio at `http://127.0.0.1:8765`, creates its local workspace on first run, and opens your browser.

Install only the cloud integrations you need:

```powershell
pip install "datacoolie-studio[s3]"
pip install "datacoolie-studio[minio]"
pip install "datacoolie-studio[adls]"
pip install "datacoolie-studio[onelake]"
pip install "datacoolie-studio[gcs]"
```

Databricks SDK support is included in the base installation. Use
`pip install "datacoolie-studio[cloud]"` to install every other cloud
integration.

## Create your first workspace

1. Create a **Project**
2. Add an **Environment** such as `dev`, `test`, or `prod`
3. Add a metadata file or scan a DataCoolie project
4. Add ETL logs for Monitoring
5. Add Python code artifacts when metadata references Python functions
6. Open **Metadata**, **Assets**, **Lineage**, or **Monitoring**

Metadata is required. Logs and code artifacts are optional.

## Configure Studio

Studio stores local state under `~\.datacoolie\datacoolie-studio\`:

```text
db\studio.db
backups\
cache\
logs\
```

Common launcher options:

```powershell
datacoolie-studio --port 8765
datacoolie-studio --host 127.0.0.1
datacoolie-studio --db .\.scratch\studio.db
datacoolie-studio --database-url "postgresql+psycopg://user:password@host:5432/datacoolie_studio"
datacoolie-studio --no-open
```

You can also configure storage with environment variables:

| Variable | Purpose |
|---|---|
| `DATACOOLIE_STUDIO_DB` | SQLite workspace database path |
| `DATACOOLIE_STUDIO_DATABASE_URL` | SQLAlchemy database URL; overrides the SQLite path |
| `DATACOOLIE_STUDIO_RESULT_CACHE_URL` | Result-cache SQLite URL |

Studio binds to `127.0.0.1` by default. Choose a shared database and review network access before hosting it for multiple users.

## Develop from source

Run the backend directly from `src`. This assumes the active Python environment already contains the dependencies declared in `pyproject.toml`.

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m uvicorn datacoolie_studio.main:app `
  --reload `
  --host 127.0.0.1 `
  --port 8765
```

Run the frontend in another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite sends API requests to the backend at `http://127.0.0.1:8765`.

Build the frontend into the Python package:

```powershell
cd frontend
npm run build
```

Run repository checks:

```powershell
.\scripts\verify.ps1
.\scripts\verify.ps1 -Mode Full
```

The default check covers architecture, packaged static assets, security, API contracts, frontend tests, and the production build. Full mode also runs the complete backend test suite.
