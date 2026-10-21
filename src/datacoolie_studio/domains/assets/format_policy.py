"""Assets-owned format aliases; presentation families do not change identity."""
from __future__ import annotations

import re

_ALIASES = {'deltalake': 'delta', 'apacheiceberg': 'iceberg',
            'apacheparquet': 'parquet', 'apacheavro': 'avro', 'apacheorc': 'orc'}


def canonical_format(value: str | None) -> str | None:
    text = (value or '').strip().casefold()
    if text in ('', 'unknown', 'null', 'none'):
        return None
    return _ALIASES.get(re.sub(r'[_\s-]', '', text), text)


def canonical_format_sql(expression: str) -> str:
    text = f"LOWER(NULLIF(TRIM({expression}),''))"
    compact = f"regexp_replace({text},'[_\\s-]','','g')"
    aliases = ' '.join(f"WHEN '{key}' THEN '{value}'" for key, value in _ALIASES.items())
    return f"CASE {compact} {aliases} ELSE {text} END"
