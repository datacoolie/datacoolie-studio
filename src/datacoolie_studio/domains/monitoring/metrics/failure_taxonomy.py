from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class FailureRule:
    rule_id: str
    category: str
    priority: int
    patterns: tuple[str, ...]
    tags: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FailureClassification:
    category: str
    tags: tuple[str, ...]
    rule_id: str | None


FAILURE_RULES: tuple[FailureRule, ...] = (
    FailureRule("limits.http_429", "Timeout / throttling", 130, (r"\bhttp(?: status)?[ /:]?429\b", r"\b429 too many requests\b"), ("HTTP 429", "Rate limit")),
    FailureRule("authorization.http_403", "Authorization", 130, (r"\bhttp(?: status)?[ /:]?403\b", r"\b403 forbidden\b"), ("HTTP 403",)),
    FailureRule("authentication.http_401", "Authentication", 130, (r"\bhttp(?: status)?[ /:]?401\b", r"\b401 unauthorized\b"), ("HTTP 401",)),
    FailureRule("resource.out_of_memory", "Resource / capacity", 125, (r"\boutofmemoryerror\b", r"\bout of memory\b", r"\boom killed\b", r"\bmemory limit exceeded\b"), ("Memory",)),
    FailureRule("resource.disk_full", "Resource / capacity", 125, (r"\bno space left on device\b", r"\bdisk (?:is )?full\b", r"\bstorage (?:limit|capacity) exceeded\b"), ("Storage",)),
    FailureRule("concurrency.deadlock", "Concurrency / conflict", 125, (r"\bdeadlock(?:ed)?\b",), ("Deadlock",)),
    FailureRule("concurrency.delta_write_conflict", "Concurrency / conflict", 121, (r"\bdelta_concurrent\w*\b",), ("Concurrent write", "Delta")),
    FailureRule("concurrency.write_conflict", "Concurrency / conflict", 120, (r"\bconcurrent (?:write|modification|update)\b", r"\bwrite conflict\b", r"\bversion conflict\b"), ("Concurrent write",)),
    FailureRule("dependency.module_not_found", "Dependency", 120, (r"\bmodulenotfounderror\b", r"\bno module named\b", r"\bmodule not found\b"), ("Module",)),
    FailureRule("connectivity.connection_refused", "Connectivity", 115, (r"\bconnection refused\b", r"\bfailed to establish a new connection\b"), ("Connection refused",)),
    FailureRule("connectivity.dns", "Connectivity", 115, (r"\bgetaddrinfo\b", r"\bdns (?:lookup|resolution|failure|error)\b", r"\bname or service not known\b", r"\bhost not found\b"), ("DNS",)),
    FailureRule("schema.cast", "Schema / format", 115, (r"\bcannot cast\b", r"\bcast error\b", r"\bconversion error\b"), ("Cast", "Data type")),
    FailureRule("schema.type_mismatch", "Schema / format", 115, (r"\btype mismatch\b", r"\bdata[ _-]?type mismatch\b", r"\bincompatible (?:data )?types?\b", r"\bfailed to merge fields\b"), ("Data type",)),
    FailureRule("missing.object", "Missing object", 110, (
        r"\b(?:table|view|file|path|column|database|schema)\b.{0,48}\b(?:not found|does not exist|missing)\b",
        r"\bno such (?:table|view|file|path|column|database|schema)\b",
        r"\btable or view\b.{0,48}\bdoes not exist\b",
        r"\bpath does not exist\b",
        r"\bmissing (?:delta )?(?:table|view|file|path|column|database|schema|object)\b",
    ), ("Object lookup",)),
    FailureRule("format.parse", "Schema / format", 110, (r"\bparse(?:r|ing)? (?:error|failed|failure)\b", r"\bmalformed (?:json|csv|xml|record|payload)\b", r"\bcorrupt(?:ed)? (?:file|parquet|record|data)\b", r"\binvalid (?:json|csv|xml|parquet)\b"), ("Parsing",)),
    FailureRule("quality.assertion", "Data quality", 110, (r"\bassertion(?:error)?\b", r"\bdata quality (?:check|rule|validation) failed\b", r"\bexpectation failed\b"), ("Assertion",)),
    FailureRule("authorization.permission", "Authorization", 105, (r"\bpermission denied\b", r"\baccess denied\b", r"\binsufficient privileges?\b", r"\bnot authorized\b", r"\bforbidden\b"), ("Permission",)),
    FailureRule("limits.rate_limit", "Timeout / throttling", 105, (r"\brate limit(?:ed| exceeded)?\b", r"\btoo many requests\b", r"\bthrottl(?:e|ed|ing)\b", r"\bquota exceeded\b"), ("Rate limit",)),
    FailureRule("connectivity.reset", "Connectivity", 105, (r"\bconnection reset\b", r"\bnetwork is unreachable\b", r"\bhost unreachable\b", r"\bbroken pipe\b"), ("Network",)),
    FailureRule("limits.timeout", "Timeout / throttling", 100, (r"\btimed out\b", r"\btimeout(?:error| exception)?\b", r"\bdeadline exceeded\b", r"\brequest timeout\b"), ("Timeout",)),
    FailureRule("authentication.oauth", "Authentication", 95, (r"\boauth2?\b", r"\binvalid[_ ]client\b"), ("OAuth",)),
    FailureRule("authentication.credential", "Authentication", 95, (r"\bauthentication failed\b", r"\binvalid credentials?\b", r"\bexpired (?:credential|token)\b", r"\binvalid token\b", r"\btoken expired\b"), ("Credential",)),
    FailureRule("configuration.missing", "Configuration", 95, (r"\bmissing (?:required )?(?:configuration|config|setting|option|parameter)\b", r"\brequired (?:configuration|config|setting|option|parameter)\b.{0,32}\bmissing\b"), ("Missing configuration",)),
    FailureRule("configuration.invalid", "Configuration", 90, (r"\binvalid (?:configuration|config|setting|option|parameter)\b", r"\bunsupported (?:configuration|setting|option|parameter)\b", r"\bconfiguration error\b"), ("Invalid configuration",)),
    FailureRule("dependency.package_required", "Dependency", 90, (r"\b(?:pip|conda|poetry)\s+install\b", r"\brequired package\b", r"\bmissing package\b"), ("Package",)),
    FailureRule("schema.incompatible", "Schema / format", 90, (r"\bincompatible schema\b", r"\bschema mismatch\b", r"\bschema evolution (?:error|failed|failure)\b", r"\bis not a delta table\b"), ("Schema",)),
    FailureRule("quality.constraint", "Data quality", 90, (r"\bconstraint (?:violation|failed|failure)\b", r"\bvalidation (?:error|failed|failure)\b", r"\binvalid (?:value|record|row|data)\b"), ("Validation",)),
    FailureRule("quality.null_duplicate", "Data quality", 90, (r"\bnull value\b.{0,40}\bnot allowed\b", r"\bduplicate (?:key|record|row)\b", r"\buniqueness (?:check|constraint) failed\b"), ("Invalid data",)),
    FailureRule("resource.executor", "Resource / capacity", 85, (r"\bexecutor lost\b", r"\bresource exhausted\b", r"\binsufficient resources?\b", r"\btask killed due to resource\b"), ("Compute",)),
    FailureRule("concurrency.lock", "Concurrency / conflict", 85, (r"\block (?:wait )?timeout\b", r"\bcould not obtain lock\b", r"\boptimistic concurrency\b"), ("Lock",)),
    FailureRule("runtime.syntax", "Runtime / code", 80, (r"\bsyntaxerror\b", r"\bsyntax error\b", r"\bparserexception\b"), ("Syntax",)),
    FailureRule("runtime.reference", "Runtime / code", 80, (r"\bnameerror\b", r"\battributeerror\b", r"\bnullpointerexception\b", r"\bkeyerror\b", r"\bindexerror\b"), ("Code",)),
    FailureRule("runtime.execution", "Runtime / code", 60, (r"\bexecution (?:error|failed|failure)\b", r"\bruntimeerror\b", r"\btask execution failed\b"), ("Execution",)),
)

FAILURE_RULES = tuple(sorted(FAILURE_RULES, key=lambda rule: (-rule.priority, rule.rule_id)))
_COMPILED_RULES = tuple(
    (
        rule,
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in rule.patterns),
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in rule.excludes),
    )
    for rule in FAILURE_RULES
)
_LIST_LIKE_PATTERN = re.compile(r"^[a-z0-9_:\-./]+(?:\s*;\s*[a-z0-9_:\-./]+)+$", re.IGNORECASE)


def classify_failure(message: str, *, all_evidence: str | None = None) -> FailureClassification:
    normalized = str(message or "").strip()
    if not normalized or normalized.lower() == "none":
        return FailureClassification("Unspecified", (), None)
    if _LIST_LIKE_PATTERN.fullmatch(normalized):
        return FailureClassification("Other", (), None)

    primary_matches = _matching_rules(normalized)
    primary = primary_matches[0] if primary_matches else None
    category = primary.category if primary else "Other"
    evidence_matches = _matching_rules(str(all_evidence or normalized))
    tags = {
        tag
        for rule in evidence_matches
        for tag in (*(() if rule.category == category else (rule.category,)), *rule.tags)
        if tag != category
    }
    return FailureClassification(
        category=category,
        tags=tuple(sorted(tags))[:5],
        rule_id=primary.rule_id if primary else None,
    )


def categorize_failure(message: str) -> str:
    return classify_failure(message).category


def _matching_rules(message: str) -> list[FailureRule]:
    return [
        rule
        for rule, patterns, excludes in _COMPILED_RULES
        if any(pattern.search(message) for pattern in patterns)
        and not any(pattern.search(message) for pattern in excludes)
    ]
