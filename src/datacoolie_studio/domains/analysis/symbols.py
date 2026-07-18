from __future__ import annotations

import ast
from typing import Any

import libcst as cst


UNKNOWN = object()


def evaluate_string(
    node: cst.BaseExpression,
    symbols: dict[str, str],
    context: dict[str, Any],
) -> str | object:
    if isinstance(node, cst.SimpleString):
        try:
            value = ast.literal_eval(node.value)
        except (SyntaxError, ValueError):
            return UNKNOWN
        return value if isinstance(value, str) else UNKNOWN
    if isinstance(node, cst.Name):
        return symbols.get(node.value, _context_value(context, node.value))
    if isinstance(node, cst.Attribute):
        dotted = dotted_name(node)
        return _context_value(context, dotted) if dotted else UNKNOWN
    if isinstance(node, cst.BinaryOperation) and isinstance(node.operator, cst.Add):
        left = evaluate_string(node.left, symbols, context)
        right = evaluate_string(node.right, symbols, context)
        return f"{left}{right}" if isinstance(left, str) and isinstance(right, str) else UNKNOWN
    if isinstance(node, cst.FormattedString):
        parts: list[str] = []
        for part in node.parts:
            if isinstance(part, cst.FormattedStringText):
                parts.append(part.value)
                continue
            value = evaluate_string(part.expression, symbols, context)
            if not isinstance(value, str):
                return UNKNOWN
            parts.append(value)
        return "".join(parts)
    if isinstance(node, cst.ConcatenatedString):
        left = evaluate_string(node.left, symbols, context)
        right = evaluate_string(node.right, symbols, context)
        return f"{left}{right}" if isinstance(left, str) and isinstance(right, str) else UNKNOWN
    if isinstance(node, cst.Call) and isinstance(node.func, cst.Attribute):
        base = evaluate_string(node.func.value, symbols, context)
        method = node.func.attr.value
        if isinstance(base, str) and method in {"strip", "lstrip", "rstrip"}:
            chars = None
            if node.args:
                evaluated = evaluate_string(node.args[0].value, symbols, context)
                if not isinstance(evaluated, str):
                    return UNKNOWN
                chars = evaluated
            return getattr(base, method)(chars)
        if method == "get" and node.args:
            dotted = dotted_name(node.func.value)
            key = evaluate_string(node.args[0].value, symbols, context)
            if dotted and isinstance(key, str):
                resolved = _context_value(context, f"{dotted}.{key}")
                if isinstance(resolved, str):
                    return resolved
                if len(node.args) > 1:
                    return evaluate_string(node.args[1].value, symbols, context)
    return UNKNOWN


def dotted_name(node: cst.CSTNode) -> str | None:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr.value}" if parent else None
    return None


def _context_value(context: dict[str, Any], dotted: str | None) -> str | object:
    if not dotted:
        return UNKNOWN
    if dotted in context and isinstance(context[dotted], (str, int, float)):
        return str(context[dotted])
    current: Any = context
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return UNKNOWN
        current = current[part]
    return str(current) if isinstance(current, (str, int, float)) else UNKNOWN
