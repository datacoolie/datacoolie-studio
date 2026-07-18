from __future__ import annotations

import ast
from dataclasses import asdict
from typing import Any

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from datacoolie_studio.domains.analysis.models import AnalysisResult, InputEvidence, SourceLocation
from datacoolie_studio.domains.analysis.sql import analyze_sql
from datacoolie_studio.domains.analysis.symbols import dotted_name, evaluate_string

SQL_CALL_NAMES = {"execute_sql", "sql", "execute"}
TABLE_CALL_NAMES = {"table", "read_table", "scan_table"}
READ_CONTEXT_BUILDER_METHOD_NAMES = {"format", "option", "options", "schema"}
PATH_METHOD_NAMES = {"load", "csv", "json", "parquet", "orc", "text", "binaryFile"}
PATH_FUNCTION_NAMES = {
    "read_csv",
    "scan_csv",
    "read_parquet",
    "scan_parquet",
    "read_json",
    "read_ndjson",
    "scan_ndjson",
    "read_ipc",
    "scan_ipc",
    "read_delta",
    "scan_delta",
    "read_excel",
    "read_feather",
    "scan_pyarrow_dataset",
}


def analyze_python_function(
    source: str,
    function_path: str,
    *,
    module_name: str | None = None,
    source_path: str | None = None,
    context: dict[str, Any] | None = None,
    max_helper_depth: int = 4,
) -> AnalysisResult:
    result = AnalysisResult()
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError as exc:
        result.diagnostics.append({
            "severity": "warning",
            "code": "python_parse_error",
            "message": str(exc),
        })
        return result
    functions = {
        statement.name.value: statement
        for statement in module.body
        if isinstance(statement, cst.FunctionDef)
    }
    function_name = function_path.rsplit(".", 1)[-1]
    if function_name not in functions:
        result.diagnostics.append({
            "severity": "warning",
            "code": "python_function_not_found",
            "message": f"Function not found: {function_path}",
        })
        return result
    visited: set[str] = set()

    def visit_function(name: str, depth: int) -> None:
        if name in visited or depth > max_helper_depth:
            return
        function = functions.get(name)
        if function is None:
            return
        visited.add(name)
        function_source = _normalized_function_source(module.code_for_node(function), name)
        function_module = cst.parse_module(function_source)
        wrapper = MetadataWrapper(function_module)
        visitor = _FunctionVisitor(
            result,
            function_module,
            module_name,
            source_path,
            _function_path(module_name, name),
            context or {},
        )
        wrapper.visit(visitor)
        for helper_name in sorted(visitor.helper_calls):
            visit_function(helper_name, depth + 1)

    visit_function(function_name, 0)
    result.inputs = _deduplicate_inputs(result.inputs)
    return result


class _FunctionVisitor(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(
        self,
        result: AnalysisResult,
        module: cst.Module,
        module_name: str | None,
        source_path: str | None,
        function_path: str | None,
        context: dict[str, Any],
    ) -> None:
        self.result = result
        self.module = module
        self.module_name = module_name
        self.source_path = source_path
        self.function_path = function_path
        self.context = context
        self.symbols: dict[str, str] = {}
        self.spark_session_symbols: set[str] = set()
        self.read_context_symbols: set[str] = set()
        self.sql_executor_symbols: set[str] = set()
        self.recorded_loads: set[int] = set()
        self.helper_calls: set[str] = set()

    def visit_Assign(self, node: cst.Assign) -> None:
        value = evaluate_string(node.value, self.symbols, self.context)
        for target in node.targets:
            if isinstance(target.target, cst.Name):
                name = target.target.value
                self._track_context_symbol(name, node.value)
                if isinstance(value, str):
                    self.symbols[name] = value

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        if not isinstance(node.target, cst.Name) or node.value is None:
            return
        self._track_context_symbol(node.target.value, node.value)
        value = evaluate_string(node.value, self.symbols, self.context)
        if isinstance(value, str):
            self.symbols[node.target.value] = value

    def visit_Call(self, node: cst.Call) -> None:
        call_name = _call_name(node)
        receiver_name = _call_receiver_name(node)
        sql_expression = _call_argument(node, keyword_names=("sql", "query", "statement"))
        if _is_sql_call(call_name, receiver_name, self.spark_session_symbols, self.sql_executor_symbols) and sql_expression is not None:
            self._record_sql(node, sql_expression)
            return
        if call_name == "createOrReplaceTempView" and node.args:
            self._record_temp_view(node)
            return
        table_expression = _call_argument(node, keyword_names=("table", "tableName", "name"))
        if _is_table_call(call_name, receiver_name, self.read_context_symbols) and table_expression is not None:
            self._record_table(node, table_expression, "python_table")
            return
        path_expression = _call_argument(node, keyword_names=("path", "uri", "file", "filepath", "source"))
        if (
            _is_path_call(call_name, receiver_name, self.read_context_symbols)
            and id(node) not in self.recorded_loads
            and path_expression is not None
        ):
            self._record_path(node, path_expression, _path_provenance(call_name))
            return
        if isinstance(node.func, cst.Name):
            self.helper_calls.add(node.func.value)

    def _record_sql(self, node: cst.Call, expression: cst.BaseExpression) -> None:
        sql = evaluate_string(expression, self.symbols, self.context)
        if not isinstance(sql, str):
            self._diagnostic(
                node,
                "dynamic_sql",
                "SQL expression could not be resolved statically",
                expression,
            )
            return
        analyzed = analyze_sql(sql)
        for item in analyzed.inputs:
            item.provenance = "python_sql"
            if item.location is not None:
                item.details["resolved_sql_location"] = asdict(item.location)
            item.location = self._location(expression)
            item.details["match_precision"] = "detection_expression"
            temp_view = self.result.temp_views.get(item.value.lower())
            self.result.inputs.append(temp_view if temp_view else item)
        self.result.diagnostics.extend(analyzed.diagnostics)

    def _record_temp_view(self, node: cst.Call) -> None:
        view_name_expression = _call_argument(node, keyword_names=("name", "view_name", "viewName"))
        view_name = evaluate_string(view_name_expression, self.symbols, self.context) if view_name_expression is not None else None
        path_call = _find_path_call_in_chain(node.func)
        path_expression = _call_argument(path_call, keyword_names=("path", "uri", "file", "filepath", "source")) if path_call is not None else None
        if not isinstance(view_name, str) or path_call is None or path_expression is None:
            self._diagnostic(
                node,
                "dynamic_temp_view",
                "Temp-view registration could not be resolved statically",
                node,
            )
            return
        self.recorded_loads.add(id(path_call))
        path = evaluate_string(path_expression, self.symbols, self.context)
        if not isinstance(path, str):
            self._diagnostic(
                path_call,
                "dynamic_path",
                "Loaded path could not be resolved statically",
                path_expression,
            )
            return
        evidence = InputEvidence(
            kind="path",
            value=path,
            provenance="python_temp_view",
            location=self._location(path_expression),
            details={"temp_view": view_name},
        )
        self.result.temp_views[view_name.lower()] = evidence
        self.result.inputs.append(evidence)

    def _record_path(self, node: cst.Call, expression: cst.BaseExpression, provenance: str) -> None:
        path = evaluate_string(expression, self.symbols, self.context)
        if isinstance(path, str):
            self.result.inputs.append(InputEvidence(
                kind="path",
                value=path,
                provenance=provenance,
                location=self._location(expression),
                details={"match_precision": "detection_expression"},
            ))
        else:
            self._diagnostic(
                node,
                "dynamic_path",
                "Loaded path could not be resolved statically",
                expression,
            )

    def _record_table(self, node: cst.Call, expression: cst.BaseExpression, provenance: str) -> None:
        table_value = evaluate_string(expression, self.symbols, self.context)
        if not isinstance(table_value, str):
            self._diagnostic(
                node,
                "dynamic_table",
                "Table reference could not be resolved statically",
                expression,
            )
            return
        table_parts = _normalize_table_parts(table_value)
        if not table_parts:
            self._diagnostic(
                node,
                "dynamic_table",
                "Table reference could not be resolved statically",
                expression,
            )
            return
        if _looks_like_path_reference(table_parts):
            self.result.inputs.append(InputEvidence(
                kind="path",
                value=table_value,
                provenance=provenance,
                location=self._location(expression),
                details={"match_precision": "detection_expression"},
            ))
            return
        normalized = ".".join(table_parts)
        catalog = ".".join(table_parts[:-2]) if len(table_parts) > 2 else None
        schema_name = table_parts[-2] if len(table_parts) >= 2 else None
        table = table_parts[-1]
        self.result.inputs.append(InputEvidence(
            kind="table",
            value=normalized,
            provenance=provenance,
            catalog=catalog,
            schema_name=schema_name,
            table=table,
            location=self._location(expression),
            details={"match_precision": "detection_expression"},
        ))

    def _location(self, node: cst.CSTNode) -> SourceLocation:
        position = self.get_metadata(PositionProvider, node)
        return SourceLocation(
            module=self.module_name,
            path=self.source_path,
            function_path=self.function_path,
            line=position.start.line,
            column=position.start.column,
            end_line=position.end.line,
            end_column=position.end.column,
            coordinate_space="function_source",
        )

    def _diagnostic(
        self,
        node: cst.CSTNode,
        code: str,
        message: str,
        expression: cst.CSTNode | None = None,
    ) -> None:
        self.result.diagnostics.append({
            "severity": "info",
            "code": code,
            "message": message,
            "location": asdict(self._location(node)),
            "details": {
                "expression": self.module.code_for_node(expression).strip()
                if expression is not None
                else None,
            },
        })

    def _track_context_symbol(self, name: str, expression: cst.BaseExpression) -> None:
        if self._is_spark_session_expression(expression):
            self.spark_session_symbols.add(name)
        else:
            self.spark_session_symbols.discard(name)
        if self._is_read_context_expression(expression):
            self.read_context_symbols.add(name)
        else:
            self.read_context_symbols.discard(name)
        if self._is_sql_executor_expression(expression):
            self.sql_executor_symbols.add(name)
        else:
            self.sql_executor_symbols.discard(name)

    def _is_spark_session_expression(self, expression: cst.BaseExpression) -> bool:
        if isinstance(expression, cst.Name):
            return expression.value in self.spark_session_symbols
        dotted = _expression_name(expression)
        if not dotted:
            return False
        normalized = dotted.lower()
        return normalized == "spark" or normalized.endswith(".spark")

    def _is_read_context_expression(self, expression: cst.BaseExpression) -> bool:
        if isinstance(expression, cst.Name):
            return expression.value in self.read_context_symbols
        dotted = _expression_name(expression)
        if dotted and _receiver_has_read_context(dotted):
            return True
        if isinstance(expression, cst.Call):
            call_name = _call_name(expression)
            if call_name in READ_CONTEXT_BUILDER_METHOD_NAMES and isinstance(expression.func, cst.Attribute):
                return self._is_read_context_expression(expression.func.value)
        return False

    def _is_sql_executor_expression(self, expression: cst.BaseExpression) -> bool:
        if isinstance(expression, cst.Name):
            return expression.value in self.sql_executor_symbols
        dotted = _expression_name(expression)
        if dotted and _receiver_has_sql_context(dotted):
            return True
        if isinstance(expression, cst.Call):
            return _call_name(expression) == "SQLContext"
        return False


def _call_name(node: cst.Call) -> str | None:
    if isinstance(node.func, cst.Name):
        return node.func.value
    if isinstance(node.func, cst.Attribute):
        return node.func.attr.value
    return dotted_name(node.func)


def _call_receiver_name(node: cst.Call) -> str | None:
    if isinstance(node.func, cst.Attribute):
        return _expression_name(node.func.value)
    return None


def _expression_name(node: cst.CSTNode) -> str | None:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        parent = _expression_name(node.value)
        return f"{parent}.{node.attr.value}" if parent else node.attr.value
    if isinstance(node, cst.Call):
        return _expression_name(node.func)
    return None


def _call_argument(
    node: cst.Call | None,
    *,
    position: int = 0,
    keyword_names: tuple[str, ...] = (),
) -> cst.BaseExpression | None:
    if node is None:
        return None
    positional = [arg for arg in node.args if arg.keyword is None]
    if len(positional) > position:
        return positional[position].value
    for arg in node.args:
        if arg.keyword is None:
            continue
        if arg.keyword.value in keyword_names:
            return arg.value
    return None


def _is_sql_call(
    call_name: str | None,
    receiver_name: str | None,
    spark_session_symbols: set[str],
    sql_executor_symbols: set[str],
) -> bool:
    if call_name not in SQL_CALL_NAMES:
        return False
    if call_name == "execute_sql":
        return True
    if call_name == "sql":
        return True
    if call_name == "execute":
        if receiver_name is None:
            return False
        if receiver_name in sql_executor_symbols:
            return True
        return _receiver_has_sql_context(receiver_name)
    if receiver_name is None:
        return True
    return receiver_name in sql_executor_symbols


def _is_table_call(call_name: str | None, receiver_name: str | None, read_context_symbols: set[str]) -> bool:
    if call_name not in TABLE_CALL_NAMES:
        return False
    if call_name in {"read_table", "scan_table"}:
        return True
    if receiver_name is None:
        return False
    if receiver_name in read_context_symbols:
        return True
    normalized = receiver_name.lower()
    if normalized == "spark" or normalized.endswith(".spark"):
        return True
    return _receiver_has_read_context(normalized)


def _is_path_call(call_name: str | None, receiver_name: str | None, read_context_symbols: set[str]) -> bool:
    if call_name is None:
        return False
    if call_name in PATH_FUNCTION_NAMES:
        return True
    if call_name == "load":
        return True
    if call_name in PATH_METHOD_NAMES:
        if receiver_name is None:
            return False
        if receiver_name in read_context_symbols:
            return True
        return _receiver_has_read_context(receiver_name.lower())
    return False


def _path_provenance(call_name: str | None) -> str:
    normalized = call_name.lower() if call_name else None
    if normalized in {"load", "csv", "json", "parquet", "orc", "text", "binaryfile"}:
        return f"python_read_{normalized}"
    if normalized:
        return f"python_{normalized}"
    return "python_path"


def _receiver_has_read_context(receiver_name: str) -> bool:
    normalized = receiver_name.lower()
    return (
        normalized == "read"
        or normalized.endswith(".read")
        or ".read." in normalized
        or normalized.startswith("read.")
    )


def _receiver_has_sql_context(receiver_name: str) -> bool:
    normalized = receiver_name.lower()
    return (
        normalized == "sql_context"
        or normalized == "sqlcontext"
        or normalized.endswith(".sql_context")
        or normalized.endswith(".sqlcontext")
        or normalized.endswith("_sql_context")
        or normalized.endswith("_sqlcontext")
        or ".sql_context." in normalized
        or ".sqlcontext." in normalized
    )


def _normalize_table_parts(value: str) -> list[str]:
    parts = []
    for part in str(value).strip().split("."):
        token = part.strip().strip('"`[]')
        if token:
            parts.append(token)
    return parts


def _looks_like_path_reference(parts: list[str]) -> bool:
    if not parts:
        return False
    value = ".".join(parts).lower()
    return "://" in value or value.startswith(("/", "\\", "dbfs:", "s3:", "abfss:", "gs:"))


def _find_call_in_chain(node: cst.CSTNode, name: str) -> cst.Call | None:
    current = node
    while True:
        if isinstance(current, cst.Attribute):
            current = current.value
            continue
        if isinstance(current, cst.Call):
            if _call_name(current) == name:
                return current
            current = current.func
            continue
        return None


def _find_path_call_in_chain(node: cst.CSTNode) -> cst.Call | None:
    current = node
    while True:
        if isinstance(current, cst.Attribute):
            current = current.value
            continue
        if isinstance(current, cst.Call):
            call_name = _call_name(current)
            if call_name in PATH_METHOD_NAMES or call_name in PATH_FUNCTION_NAMES:
                return current
            current = current.func
            continue
        return None


def _deduplicate_inputs(inputs: list[InputEvidence]) -> list[InputEvidence]:
    unique: list[InputEvidence] = []
    seen: set[tuple[str, str, str, int | None, int | None, int | None, int | None]] = set()
    for item in inputs:
        location = item.location
        key = (
            item.kind,
            item.value,
            location.function_path if location else "",
            location.line if location else None,
            location.column if location else None,
            location.end_line if location else None,
            location.end_column if location else None,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _function_path(module_name: str | None, function_name: str) -> str | None:
    return f"{module_name}.{function_name}" if module_name else function_name


def _normalized_function_source(source: str, function_name: str) -> str:
    """Match the preview's function-only source coordinate space."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return source
    function = next(
        (
            node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
        ),
        None,
    )
    if function is None:
        return source
    decorator_lines = [decorator.lineno for decorator in function.decorator_list]
    start_line = min([function.lineno, *decorator_lines])
    end_line = int(getattr(function, "end_lineno", None) or function.lineno)
    return "\n".join(source.splitlines()[start_line - 1:end_line])
