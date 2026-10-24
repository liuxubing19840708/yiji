# -*- coding: utf-8 -*-
"""公式 DSL：受限表达式（simpleeval + AST 白名单，三层防护，禁 eval）。

DSL 支持：四则（+ - * / // % **）、一元正负/非、比较（== != < <= > >=）、
布尔（and/or）、三元（cond ? a : b 即 Python 的 a if cond else b）、
字段引用（Name 必须在字段注册表内）、函数白名单（IF/round/sum/min/max/abs）。

安全边界：
  1. validate_expr（保存模板时）：ast.parse 后只放行白名单节点，非法即拒；
  2. eval_expr（渲染时）：simpleeval 求值（本身禁属性访问/dunder/任意调用），
     NAMES 只给行字段值，函数只给白名单；
  3. 兜底：渲染单列求值异常 → 落占位符，不影响整行。
"""
import ast

from simpleeval import SimpleEval

from apps.kanban_template.fields import KANBAN_FIELDS

MAX_EXPR_LEN = 256

# 允许的 AST 节点类型（第 1 层白名单）
_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.IfExp,
    ast.Call, ast.Name, ast.Load, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.And, ast.Or,
)

# 允许的函数（第 2 层；IF=三元函数式写法）
_FUNCTIONS = {
    'IF': lambda cond, a, b: a if cond else b,
    'round': round,
    'sum': sum,
    'min': min,
    'max': max,
    'abs': abs,
}

# 常量：数字任意；字符串限长 ≤64（用于 IF(a=='外发', ...) 之类比较，防大 payload）
_CONST_NUM_TYPES = (int, float)
_MAX_STR_CONST = 64


class FormulaError(ValueError):
    """公式非法（保存模板时报给配置端）。"""


def validate_expr(expr):
    """保存前校验：语法 + 节点白名单 + 字段白名单。非法抛 FormulaError。"""
    if not isinstance(expr, str) or not expr.strip():
        raise FormulaError('公式不能为空')
    if len(expr) > MAX_EXPR_LEN:
        raise FormulaError(f'公式过长（>{MAX_EXPR_LEN} 字符）')
    try:
        tree = ast.parse(expr, mode='eval')
    except (SyntaxError, ValueError, MemoryError, RecursionError) as e:
        raise FormulaError(f'公式语法错误：{e}') from None
    func_ids = {n.func.id for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(f'公式含不允许的语法：{type(node).__name__}')
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool) or not isinstance(
                    v, _CONST_NUM_TYPES + (str,)) or (
                    isinstance(v, str) and len(v) > _MAX_STR_CONST):
                raise FormulaError('公式只允许数字或 ≤64 字符的字符串常量')
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
                raise FormulaError('只允许调用白名单函数：' + '/'.join(_FUNCTIONS))
            if node.keywords:
                raise FormulaError('函数调用不允许关键字参数')
        if isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load):
                raise FormulaError('公式只允许引用字段')
            # 函数名（已在白名单校验过）不算字段引用
            if node.id in func_ids and node.id in _FUNCTIONS:
                continue
            if node.id not in KANBAN_FIELDS:
                raise FormulaError(f'未知字段：{node.id}')
    return expr.strip()


def eval_expr(expr, names):
    """渲染期求值。names = {字段key: 数值}；任何异常向上抛（由渲染层落占位符）。"""
    ev = SimpleEval(functions=dict(_FUNCTIONS))
    ev.names = {k: v for k, v in names.items() if k in KANBAN_FIELDS}
    return ev.eval(expr)
