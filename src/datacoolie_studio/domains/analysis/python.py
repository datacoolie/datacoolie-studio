from __future__ import annotations

from dataclasses import asdict
from typing import Any

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from datacoolie_studio.domains.analysis.models import AnalysisResult, InputEvidence, SourceLocation
from datacoolie_studio.domains.analysis.sql import analyze_sql
from datacoolie_studio.domains.analysis.symbols import dotted_name, evaluate_string


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
        wrapper = MetadataWrapper(cst.Module(body=[function]))
        visitor = _FunctionVisitor(result, module, module_name, source_path, context or {})
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
        context: dict[str, Any],
    ) -> None:
        self.result = result
        self.module = module
        self.module_name = module_name
        self.source_path = source_path
        self.context = context
        self.symbols: dict[str, str] = {}
        self.recorded_loads: set[int] = set()
        self.helper_calls: set[str] = set()

    def visit_Assign(self, node: cst.Assign) -> None:
        value = evaluate_string(node.value, self.symbols, self.context)
        if not isinstance(value, str):
            return
        for target in node.targets:
            if isinstance(target.target, cst.Name):
                self.symbols[target.target.value] = value

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        if not isinstance(node.target, cst.Name) or node.value is None:
            return
        value = evaluate_string(node.value, self.symbols, self.context)
        if isinstance(value, str):
            self.symbols[node.target.value] = value

    def visit_Call(self, node: cst.Call) -> None:
        call_name = _call_name(node)
        if call_name in {"execute_sql", "sql"} and node.args:
            self._record_sql(node, node.args[0].value)
            return
        if call_name == "createOrReplaceTempView" and node.args:
            self._record_temp_view(node)
            return
        if call_name == "load" and id(node) not in self.recorded_loads and node.args:
            self._record_path(node, node.args[0].value, "python_load")
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
            item.location = self._location(node)
            temp_view = self.result.temp_views.get(item.value.lower())
            self.result.inputs.append(temp_view if temp_view else item)
        self.result.diagnostics.extend(analyzed.diagnostics)

    def _record_temp_view(self, node: cst.Call) -> None:
        view_name = evaluate_string(node.args[0].value, self.symbols, self.context)
        load_call = _find_call_in_chain(node.func, "load")
        if not isinstance(view_name, str) or load_call is None or not load_call.args:
            self._diagnostic(
                node,
                "dynamic_temp_view",
                "Temp-view registration could not be resolved statically",
                node,
            )
            return
        self.recorded_loads.add(id(load_call))
        path = evaluate_string(load_call.args[0].value, self.symbols, self.context)
        if not isinstance(path, str):
            self._diagnostic(
                load_call,
                "dynamic_path",
                "Loaded path could not be resolved statically",
                load_call.args[0].value,
            )
            return
        evidence = InputEvidence(
            kind="path",
            value=path,
            provenance="python_temp_view",
            location=self._location(load_call),
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
                location=self._location(node),
            ))
        else:
            self._diagnostic(
                node,
                "dynamic_path",
                "Loaded path could not be resolved statically",
                expression,
            )

    def _location(self, node: cst.CSTNode) -> SourceLocation:
        position = self.get_metadata(PositionProvider, node)
        return SourceLocation(
            module=self.module_name,
            path=self.source_path,
            line=position.start.line,
            column=position.start.column,
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


def _call_name(node: cst.Call) -> str | None:
    if isinstance(node.func, cst.Name):
        return node.func.value
    if isinstance(node.func, cst.Attribute):
        return node.func.attr.value
    return dotted_name(node.func)


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


def _deduplicate_inputs(inputs: list[InputEvidence]) -> list[InputEvidence]:
    unique: list[InputEvidence] = []
    seen: set[tuple[str, str]] = set()
    for item in inputs:
        key = (item.kind, item.value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
