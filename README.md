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
