# Pinned v7 producer fixtures

These records were generated on 2026-10-21 through the local DataCoolie 0.2.0
`build_dataflow_entry` and `flatten_job_runtime` projectors. Studio tests read
these JSON fixtures directly, so standalone Studio verification does not import
the producer package. `schema.json` is the matching producer v7 schema.

The fixtures are intentionally small: one admitted dataflow record with a
measured `rows_written` value and one job record. Consumer tests copy and
mutate those records to cover missing, zero, legacy, and maintenance cases.
