# Databricks benchmark tools

These scripts are developer-run diagnostics, not published `datacoolie-studio` commands.
Run them from a source checkout with the backend source on `PYTHONPATH` (or from an environment
where `datacoolie-studio` is installed):

```powershell
$env:PYTHONPATH = "$PWD\src"
python scripts/benchmarks/databricks_storage.py `
  --directory-uri "dbfs:/Volumes/catalog/schema/volume/logs" `
  --file-uri "dbfs:/Volumes/catalog/schema/volume/logs/sample.jsonl" `
  --runs 5

python scripts/benchmarks/databricks_log_sync.py --source-id 42 --runs 3
```

`databricks_storage.py` measures list, stat, and read latency through the Databricks SDK and
does not write to the remote storage. It requires Databricks authentication through the SDK's
ambient configuration or `--profile`.

`databricks_log_sync.py` invokes a real refresh for an existing DBFS log source and can write
the resulting local Studio database/cache state. Use a disposable or explicitly approved
workspace when running it. Both tools print JSON and accept `--help` without contacting a
Databricks workspace.
