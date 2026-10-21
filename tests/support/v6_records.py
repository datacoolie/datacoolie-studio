"""Native source records built from producer fixtures for consumer scenarios."""
from copy import deepcopy
import json

from tests.support.v6_fixture import v6_rows


def native_record(fields):
    fields = dict(fields)
    authored = set(fields)
    kind = fields.get('_type', 'dataflow_run_log')
    filename = {'dataflow_run_log': 'dataflow_snapshot.json', 'job_run_log': 'job_snapshot.json', 'system_log': 'system_snapshot.json'}[kind]
    record = deepcopy(v6_rows(filename)[0])
    if kind == 'system_log':
        for key in ('dataflow_id', 'dataflow_run_id', 'event_name'):
            record.pop(key, None)
    if fields.get('raw_record_json'):
        record.update(json.loads(fields.pop('raw_record_json')))
    if kind == 'dataflow_run_log':
        aliases = {'source_rows_read': 'rows_read', **{f'destination_{key}': key for key in ('rows_written', 'rows_inserted', 'rows_updated', 'rows_deleted', 'files_added', 'files_removed', 'bytes_added', 'bytes_removed', 'bytes_saved')}}
        for alias, metric in aliases.items():
            if alias in fields:
                value = fields.pop(alias)
                record['metrics'][metric] = value
                if record.get('attempts'):
                    phase = 'source' if alias.startswith('source_') else 'destination'
                    record['attempts'][-1][phase][metric] = value
        for phase in ('source', 'transform', 'destination'):
            for member in ('start_time', 'end_time', 'duration_seconds', 'status', 'message', 'error_message', 'operation_type', 'operation_details'):
                alias = f'{phase}_{member}'
                if alias in fields:
                    value = fields.pop(alias)
                    if record.get('attempts'):
                        if member != 'error_message' or f'{phase}_message' not in authored:
                            record['attempts'][-1][phase]['message' if member == 'error_message' else member] = value
        if 'error_message' in fields:
            value = fields.pop('error_message')
            if 'message' not in fields:
                record['message'] = value
        fields.pop('overhead_duration_seconds', None)
    record.update(fields)
    record['log_schema_version'] = 6
    return record


def insert_native_rows(connection, table, source_id, rows, column_types):
    """Adapt authored scenario fields to native log shape at the test boundary."""
    from datacoolie_studio.domains.analytics import schema, store
    kind = 'dataflow_run_log' if table == schema.DATAFLOW_TABLE else 'job_run_log'
    native = []
    for uri, file_kind, revision, fields in rows:
        complete = native_record({'_type': kind, **fields})
        if 'log_session_id' not in fields:
            complete['log_session_id'] = None
        # Omitted report messages must remain absent, including legacy aliases.
        for name in ('message', 'start_time', 'end_time'):
            if name not in fields:
                complete.pop(name, None)
        if 'source_message' not in fields and 'attempts' not in fields and complete.get('attempts'):
            complete['attempts'][-1]['source'].pop('message', None)
        native.append((uri, file_kind, revision, complete))
    store.insert_typed_rows(connection, table, source_id, native, column_types)
