"""Parser safety: only the whitelisted grammar is accepted; nothing is eval'd."""

import ast
import pathlib

import pytest

from ailab import derive
from ailab.derive import TS, ExpressionError

from .conftest import ser


def _in(idx):
    a = TS(ser([1, 2, 3, 4, 5, 6], idx), "count")
    b = TS(ser([2, 2, 2, 2, 2, 2], idx), "count")
    return {"a": a, "b": b}


INJECTIONS = [
    "__import__('os').system('id')",
    "__import__('os')",
    "a.__class__",
    "a.__class__.__mro__[1].__subclasses__()",
    "a.s",
    "a.s.to_pickle('/tmp/x')",
    "a.s.to_csv('/tmp/pwn')",
    "open('/etc/passwd')",
    "eval('1+1')",
    "exec('import os')",
    "compile('1','x','eval')",
    "getattr(a, 's')",
    "globals()",
    "locals()",
    "vars(a)",
    "dir(a)",
    "type(a)",
    "(lambda: 1)()",
    "lambda: 1",
    "[x for x in a]",
    "{x: 1 for x in a}",
    "(x for x in a)",
    "a[0]",
    "a[1:2]",
    "a if a else b",
    "a and b",
    "a or b",
    "not a",
    "a < b",
    "a == b",
    "a in b",
    "a ** 2",
    "a ** 9999999999",
    "a // 2",
    "a % 2",
    "a @ b",
    "a << 3",
    "a & b",
    "a | b",
    "a ^ b",
    "~a",
    "'x' * 3",
    "'abc'",
    "b'abc'",
    "f'{a}'",
    "True",
    "None",
    "1j",
    "...",
    "ma(a, n=3)",
    "ma(*[a, 3])",
    "ma(**{'x': 1})",
    "a; b",
    "a\nb",
    "import os",
    "x = 1",
    "(a := 3)",
    "await a",
    "yield a",
    "ma(a, 3)(2)",
    "ma.__call__(a, 3)",
    "a.rolling(3).mean()",
    "1e999",
    "999999999999999999999999 * a",
    "\x00",
    "a +",
    "((((((",
    "",
    "   ",
]


@pytest.mark.parametrize("expr", INJECTIONS)
def test_injection_attempts_are_rejected(expr, idx):
    with pytest.raises(ExpressionError):
        derive.evaluate(expr, _in(idx))


def test_injection_does_not_touch_filesystem(idx, tmp_path):
    target = tmp_path / "pwn"
    for expr in (f"a.s.to_csv('{target}')", f"open('{target}', 'w')"):
        with pytest.raises(ExpressionError):
            derive.evaluate(expr, _in(idx))
    assert not target.exists()


def test_deeply_nested_and_huge_expressions_rejected(idx):
    with pytest.raises(ExpressionError):
        derive.evaluate("(" * 5000 + "a" + ")" * 5000, _in(idx))
    with pytest.raises(ExpressionError):
        derive.evaluate("a" + " + a" * 200, _in(idx))  # node budget
    with pytest.raises(ExpressionError):
        derive.evaluate("a +" * 400 + "a", _in(idx))  # length budget


@pytest.mark.parametrize(
    "expr",
    [
        "ma(a, 0)",
        "ma(a, -1)",
        "ma(a, 100000)",
        "ma(a, 2.5)",
        "ma(a, a)",
        "ma(a)",
        "ma(a, 3, 4)",
        "lag(3, 1)",
        "rolling_sum(a, True)",
        "nosuchfunc(a)",
        "zzz + a",
        "1 + 2",
        "sum(a)",
        "sum(a, 3)",
        "abs(1)",
    ],
)
def test_semantic_errors(expr, idx):
    with pytest.raises(ExpressionError):
        derive.evaluate(expr, _in(idx))


def test_valid_grammar_works(idx):
    inp = _in(idx)
    assert derive.evaluate("a + b", inp).s.iloc[0] == 3
    assert derive.evaluate("-a + 10", inp).s.iloc[0] == 9
    assert derive.evaluate("(a + b) * 2 - 1", inp).s.iloc[1] == 7
    assert derive.evaluate("rolling_mean(a, 3)", inp).s.iloc[2] == 2.0
    assert derive.evaluate("ma(a, 3)", inp).s.iloc[2] == 2.0
    assert derive.evaluate("rolling_sum(a, 2)", inp).s.iloc[1] == 3.0
    assert derive.evaluate("lag(a, 2)", inp).s.iloc[2] == 1.0
    assert derive.evaluate("lag(a)", inp).s.iloc[1] == 1.0
    assert derive.evaluate("diff(a)", inp).s.iloc[1] == 1.0
    assert derive.evaluate("sum(a, b, a)", inp).s.iloc[0] == 4.0
    assert derive.evaluate("pct_change(a)", inp).s.iloc[1] == pytest.approx(100.0)
    assert derive.evaluate("sqrt(a)", inp).s.iloc[3] == 2.0
    assert derive.evaluate("abs(a - 10)", inp).s.iloc[0] == 9.0


def test_referenced_names_excludes_functions():
    assert derive.referenced_names("ma(a, 3) + b / 2 - sum(c, d)") == ["a", "b", "c", "d"]
    with pytest.raises(ExpressionError):
        derive.referenced_names("__import__('os')")


def test_simpleeval_instance_has_only_whitelisted_nodes():
    ev = derive._SafeEval({})
    assert set(ev.nodes) == {ast.Name, ast.UnaryOp, ast.BinOp, ast.Call, ast.Constant}
    assert ast.Attribute not in ev.nodes and ast.Subscript not in ev.nodes


def test_source_never_uses_eval_exec_pickle():
    """Static guard over the whole package: no eval/exec/compile calls, no pickle."""
    root = pathlib.Path(derive.__file__).parent
    bad = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                if n.func.id in {"eval", "exec", "compile", "__import__"}:
                    bad.append(f"{path.name}:{n.lineno} {n.func.id}")
            if isinstance(n, ast.Import) and any(
                a.name.split(".")[0]
                in {"pickle", "dill", "joblib", "cloudpickle", "shelve", "marshal"}
                for a in n.names
            ):
                bad.append(f"{path.name}:{n.lineno} import")
            if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in {
                "pickle",
                "dill",
                "joblib",
                "cloudpickle",
                "shelve",
                "marshal",
            }:
                bad.append(f"{path.name}:{n.lineno} import-from")
            if (
                isinstance(n, ast.Attribute)
                and n.attr in {"to_pickle", "read_pickle", "loads"}
                and isinstance(n.value, ast.Name)
                and n.value.id in {"pd", "pickle"}
            ):
                bad.append(f"{path.name}:{n.lineno} {n.attr}")
    assert bad == []
