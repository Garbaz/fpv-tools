"""Conditions and other expressions over a flight's signals (reference.CONDITIONS describes them for users):
Python syntax, parsed with `ast` and evaluated element-wise over the signal arrays."""

import ast
import operator
from collections.abc import Callable
from typing import Any

import numpy as np
from numpy.typing import NDArray

from fpv_tools.blackbox import dsp
from fpv_tools.blackbox.flight import Flight

type Array = NDArray[Any]


class ExprError(ValueError):
    pass


BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}
COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


def evaluate(flight: Flight, expression: str) -> Array:
    """The expression's value per sample; constants are broadcast to the flight's length."""
    value = _Evaluator(flight).visit(_parse(expression).body)
    return np.broadcast_to(value, (len(flight),)).copy() if np.ndim(value) == 0 else np.asarray(value)


def mask(flight: Flight, expression: str | None) -> NDArray[np.bool_]:
    if not expression:
        return np.ones(len(flight), dtype=bool)
    value = evaluate(flight, expression)
    if value.dtype != bool:
        raise ExprError(f"{expression!r} is not a condition (it gives {value.dtype} values)")
    return value


def _parse(expression: str) -> ast.Expression:
    try:
        return ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ExprError(f"cannot parse {expression!r}: {e.msg}") from None


def _dotted(node: ast.AST) -> str | None:
    """`gyro.roll` (Attribute(Name)) or `thr` (Name) as a signal name; None for anything else."""
    match node:
        case ast.Name(id=name):
            return name
        case ast.Attribute(value=inner, attr=attr) if (base := _dotted(inner)) is not None:
            return f"{base}.{attr}"
        case _:
            return None


class _Evaluator(ast.NodeVisitor):
    def __init__(self, flight: Flight) -> None:
        self.flight = flight

    def generic_visit(self, node: ast.AST) -> Any:
        raise ExprError(f"unsupported syntax: {ast.unparse(node)}")

    def visit_Name(self, node: ast.Name) -> Any:
        return self.flight[node.id]

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        if (name := _dotted(node)) is None:
            raise ExprError(f"unsupported name: {ast.unparse(node)}")
        return self.flight[name]

    def visit_Constant(self, node: ast.Constant) -> Any:
        if not isinstance(node.value, int | float | str) or isinstance(node.value, bool):
            raise ExprError(f"unsupported constant: {node.value!r}")
        return node.value

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        value = self.visit(node.operand)
        match node.op:
            case ast.Not():
                return np.logical_not(value)
            case ast.USub():
                return -value
            case ast.UAdd():
                return value
            case _:
                raise ExprError(f"unsupported operator in {ast.unparse(node)}")

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        if type(node.op) not in BINARY:
            raise ExprError(f"unsupported operator in {ast.unparse(node)}")
        return BINARY[type(node.op)](self.visit(node.left), self.visit(node.right))

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        combine = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
        result = self.visit(node.values[0])
        for value in node.values[1:]:
            result = combine(result, self.visit(value))
        return result

    def visit_Compare(self, node: ast.Compare) -> Any:
        result: Any = True
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            if type(op) not in COMPARE:
                raise ExprError(f"unsupported comparison in {ast.unparse(node)}")
            right = self.visit(comparator)
            result = np.logical_and(result, COMPARE[type(op)](left, right))
            left = right
        return result

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise ExprError(f"unsupported call: {ast.unparse(node)}")
        args = [self.visit(a) for a in node.args]
        fs = self.flight.fs
        match node.func.id, args:
            case "abs", [x]:
                return np.abs(x)
            case "smooth", [x, seconds]:
                return dsp.moving_average(np.asarray(x, dtype=float), fs, float(seconds))
            case "widen", [cond, before, after]:
                return dsp.widen(np.asarray(cond, dtype=bool), fs, float(before), float(after))
            case "held", [cond, seconds]:
                return dsp.widen(np.asarray(cond, dtype=bool), fs, after=float(seconds))
            case "sustained", [cond, seconds]:
                return dsp.sustained(np.asarray(cond, dtype=bool), fs, float(seconds))
            case name, _:
                raise ExprError(
                    f"unknown function or wrong arguments: {name}({', '.join(map(ast.unparse, node.args))})"
                )
