# DataCoolie 0.2 producer capture

These JSON Lines fixtures are a deterministic capture of the framework 0.2.0
projection functions. They are consumed as static Studio test inputs, so Studio
CI does not import the sibling framework package.

The capture used framework revision `75f65139e26eb7079b7a897c877de97859c0120b`
and `src/datacoolie/logging/persistence/projection.py`, serialized with the
framework JSON writer. `manifest.json` records the capture revision, source
paths, row counts, and SHA-256 hashes.

The dataflow streams cover UUID-shaped and punctuation-bearing IDs, successful,
skipped, failed, and replay runs, phase messages, row/folder watermark bounds,
and nullable historical bounds. The job stream covers `datacoolie_version`,
`observed_at`, nullable running/pending totals, and truncation. The system
streams retain `msg`, `exc_info`, and execution context as separate fields.
