# Release notes

## Studio 0.2.0

Studio consumes DataCoolie log schemas v6 and v7. Records with older or unsupported
schema versions are excluded and counted in source and Monitoring warnings.
An old-only source stays configured and reports no admissible runs. Invalid
v6/v7 records or malformed JSON stop publication and preserve the previous cache.

Run metrics come from the root `metrics` object. V7 preserves measured zero and
uses null for unentered or unmeasured work. V7 and mixed v6/v7 totals sum available
observations: `100 + null` is `100`, and `0 + null` is `0`. A population without
observations stays null. Coverage shows measured runs against the report
population. No eligibility attribute is added to the log or native storage.
Historical v6 values and v6-only aggregate behavior remain unchanged.

Attempts, source selection,
watermark candidates and saved watermarks remain separate evidence. Monitoring
uses measured rows written, retains unavailable measurements, and avoids inferring output
from input. Phase contribution uses eligible final-attempt intervals. Recovered
attempt failures remain visible without changing a successful run into a failure.

Counts and bytes in Monitoring responses are decimal strings. Pagination controls,
durations and rates remain numeric. Raw record JSON is reconstructed from native
analytics values for bounded detail display and copying; IDs, tags and list order
remain stable, while native floating-point values may round and omitted/null
optional members may collapse.

API clients use `log_session_scope=all|null|exact`; exact scope also requires
`log_session_id`, including an explicitly empty value when that is the logged ID.
The same scope applies to reports, drawers and System Logs. Monitoring page
responses use `monitoring-page.v12` and include current schema admission evidence.

Dataflow registries use environment-scoped dataflow identity and consolidate
selected history across sessions and admitted source copies. Destination views
group physical objects through the shared Assets identity rules; weak relative
paths remain source scoped. Opening a related run preserves its own source and
session identity.
Rows written includes all measured outputs; lakehouse writes are a separate subset.

Freshness separates source checks, watermark configuration and persistence.
Saved-watermark movement uses the save-time merge baseline, including an explicit
null baseline for initialization. Native no-candidate, not-requested and
not-attempted outcomes remain distinct. Missing job snapshot time or lost child
records makes reconciliation uncertain. Diagnostics shows truncated, invalid and
omitted capture evidence separately from execution failures.

Monitoring uses consistent visual-help and hover-tooltip formatting, including
legend colors and compact durations/numbers. Horizontal bar labels fit content
within 35% of visual width. Failure, Freshness, Performance, Volume, Destination
and Diagnostics pages use three equal-height visual rows. Performance candidates
are sampled within each operation scope, including maintenance, so fixed limits
do not hide that operation's available measurements.

Diagnostic findings group confirmed issues, warnings and assessment limitations
with paged drill-in evidence. Filtered child scope produces one scope notice per
job. Capture summaries count affected records separately from individual markers.
Lakehouse catalog/database identity and derived caches refresh under the corrected
Assets and metadata-normalizer versions.

Studio now preserves log IDs as text, including UUID-shaped values, without
changing their case or punctuation. Job investigations keep source registrations
and reported log sessions separate when the same job ID appears more than once.

Monitoring shows canonical job/dataflow messages, phase messages, truncation,
DataCoolie version and job observation time. Successful and skipped messages stay
informational. Dataflow and Freshness details show request bounds, resolved
selection operators, candidate kind and provider save evidence when the producer
reports them. Missing evidence stays empty.

Job status follows the logged status, including unknown when it is missing.
Jobs, Dataflows, Performance and run history show the run's message; Freshness
shows the latest run's status and message together. System Log uses a compact
line with milliseconds, severity, logger, dataflow ID, event and the producer's
message. Source/session scope and loaded counts appear in the header; full
record details retain schema, version, job/run IDs, callsite, source file and
traceback. Copy loaded provides readable text, Copy full loaded retains full
evidence, and Copy raw record shows bounded reconstructed execution evidence.
Dataflow/run/event filters match exact values. File-read diagnostics stay visible
when no records load or only part of the evidence is available; failed reads can
be retried without treating them as an empty result.
Snapshot/part batches with all-null fields load correctly, and custom nested
attribute names retain their spelling.

Unknown runs remain visible in Monitoring charts. Freshness shows unavailable
ages as `-` and keeps the latest run status separate from the latest successful
or skipped check. Informational messages retain neutral styling even when the
text is a status word, and Maintenance run history sorts by the displayed message.
Jobs labels skipped/running/pending child-flow totals explicitly as child flows.

Studio reads, displays, edits and saves metadata and SQL. DataCoolie owns semantic
validation when preparing or running pipelines. Incomplete metadata, duplicate
identities and unknown runtime options can be saved. Studio still checks supported
file/payload formats, source routing, safe paths, write permissions, revisions and
backup integrity. Content that sheets cannot represent is preserved with its source.

The metadata validation action and
`POST /api/v1/environments/{environment_id}/metadata-editor-document/validate`
have been removed. API consumers must remove calls to this endpoint. Draft and
source-save endpoints remain available; saving does not certify runtime validity.
Source **Read Check** continues to report readability and I/O diagnostics.

Execution logs load through DuckDB's native JSON reader after schema validation.
Unknown fields and malformed current-version records stop publication with file
and line diagnostics. Studio keeps string IDs, tags and array order; native
numeric values may round and omitted optional fields may appear as null in copied
evidence.

Complete schema17 caches migrate to schema18 from stored columns. Adding an
optional field to the pinned schema also upgrades stored data without fetching
historical source logs; old rows receive null for the new field. New or changed
files populate it during normal sync. Historical backfill is not automatic.
Unsupported breaking schema changes report an upgrade error. Older or incomplete
caches can still require source logs for a rebuild.

Schema migration preserves cached source coverage, including paused sources,
and does not rewrite source logs or advance source watermarks.
The rebuild retains a checkpointed previous cache file and records its hash
before replacing the active cache. Operational rollback requires a compatible
previous Studio application; Studio 0.2.0 does not read the previous schema.
