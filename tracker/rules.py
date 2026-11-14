"""Evaluate the plain-text alert rules in config/rules.yaml."""
from __future__ import annotations

import ast
from functools import reduce

import pandas as pd

from .indicators import FUNCS


class _BoolOps(ast.NodeTransformer):
    """Let rules use 'and', 'or', 'not' on whole columns of numbers."""

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        fn = "_and" if isinstance(node.op, ast.And) else "_or"
        return ast.copy_location(ast.Call(func=ast.Name(fn, ast.Load()), args=node.values, keywords=[]), node)

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Not):
            return ast.copy_location(ast.Call(func=ast.Name("_not", ast.Load()), args=[node.operand], keywords=[]), node)
        return node


def _and(*xs):
    return reduce(lambda a, b: a & b, xs)


def _or(*xs):
    return reduce(lambda a, b: a | b, xs)


def _not(x):
    return ~x


_ALLOWED = (ast.Expression, ast.Call, ast.Name, ast.Load, ast.Constant, ast.Compare, ast.BinOp,
            ast.UnaryOp, ast.BoolOp, ast.And, ast.Or, ast.Not, ast.Gt, ast.Lt, ast.GtE, ast.LtE,
            ast.Eq, ast.NotEq, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.keyword)


def compile_rule(expr: str):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"not allowed in a rule: {type(node).__name__}")
    tree = ast.fix_missing_locations(_BoolOps().visit(tree))
    return compile(tree, "<rule>", "eval")


def check(rule: dict, candles: pd.DataFrame) -> bool:
    """True if the rule fires on the latest candle."""
    if len(candles) < 3:
        return False
    env = {c: candles[c].astype(float) for c in ["open", "high", "low", "close"]}
    env["volume"] = pd.to_numeric(candles["volume"], errors="coerce")
    env.update(FUNCS, _and=_and, _or=_or, _not=_not)
    code = rule.setdefault("_code", compile_rule(rule["when"]))
    res = eval(code, {"__builtins__": {}}, env)  # noqa: S307 - restricted AST above
    if isinstance(res, pd.Series):
        v = res.iloc[-1]
        return bool(v) if pd.notna(v) else False
    return bool(res)


def applies_to(rule: dict, name: str) -> bool:
    idx = rule.get("indices", "all")
    return idx == "all" or name in idx
