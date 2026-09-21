#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立复算报告中的派生指标，避免模型心算出错。

用法:
    python3 scripts/verify_calcs.py calcs.json                 # 校验并打印报告
    python3 scripts/verify_calcs.py calcs.json --emit-audit     # 输出可直接粘进 DATA.audit.rows 的 JSON
    python3 scripts/verify_calcs.py calcs.json --strict         # 有不一致项时退出码 1

输入 calcs.json 结构:
{
  "note": "口径说明",
  "rows": [
    {
      "id": "C1",
      "name": "三店销售额合计",
      "formula": "38766.88 + 39296.41 + 43013.36",
      "reported": "121,076.65",      # 报告正文里展示的字符串
      "decimals": 2,                  # 展示保留位数（可选）
      "unit": "currency",             # currency|percent|plain（可选，影响展示）
      "tolerance": 0.01               # 允许绝对偏差（可选，默认按 decimals 推断）
    }
  ]
}

formula 支持: 数字、+ - * / ** %、括号、千分位逗号、
以及 sum/mean/pct/delta/max/min/abs/round/sqrt/log10 等白名单函数。
变量可通过 "vars" 字段注入，例如 "vars": {"a": 10, "b": 4}。
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import re
import sys

# ---------------------------------------------------------------- 安全求值
_ALLOWED_FUNCS = {
    "sum": sum,
    "mean": lambda xs: sum(xs) / len(xs),
    "avg": lambda xs: sum(xs) / len(xs),
    "median": lambda xs: sorted(xs)[len(xs) // 2] if len(xs) % 2
        else (sorted(xs)[len(xs) // 2 - 1] + sorted(xs)[len(xs) // 2]) / 2,
    "min": lambda *a: min(a[0] if len(a) == 1 and isinstance(a[0], (list, tuple)) else a),
    "max": lambda *a: max(a[0] if len(a) == 1 and isinstance(a[0], (list, tuple)) else a),
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "log10": math.log10,
    "log": math.log,
    "pow": math.pow,
    # pct(部分, 总体) -> 百分比数值
    "pct": lambda part, whole: part / whole * 100.0,
    # delta(本期, 上期) -> 增长率百分比
    "delta": lambda cur, prev: (cur - prev) / prev * 100.0,
    # cagr(期末, 期初, 年数) -> 复合年增长率百分比
    "cagr": lambda end, begin, years: ((end / begin) ** (1.0 / years) - 1) * 100.0,
}
_ALLOWED_NAMES = set(_ALLOWED_FUNCS)

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
    ast.List, ast.Tuple, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.Pow, ast.Mod, ast.USub, ast.UAdd, ast.Call, ast.Name,
    ast.Load, ast.Index, ast.Subscript, ast.Slice,
)


def _strip_thousands(expr: str) -> str:
    """只剥离括号外的千分位逗号。

    括号内的逗号一律视为函数/列表参数分隔符，避免把
    `pct(43013.36, 121076.65)` 误删成非法语法。
    """
    out, depth = [], 0
    for i, ch in enumerate(expr):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if (ch == "," and depth == 0 and i > 0 and i + 3 < len(expr)
                and expr[i - 1].isdigit()
                and expr[i + 1:i + 4].isdigit()
                and not expr[i + 4].isdigit()):
            continue
        out.append(ch)
    return "".join(out)


def safe_eval(formula: str, variables: dict | None = None) -> float:
    """在受限白名单内求值，拒绝任何属性访问 / 导入 / 未知名字。"""
    expr = _strip_thousands(formula.replace("％", "%").strip())
    # 把字面量百分号换算成小数，例如 "16%" -> (16/100)
    expr = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", expr)
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"公式包含不允许的语法: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_NAMES:
                raise ValueError("只允许白名单函数: " + ast.dump(node)[:80])
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            if variables is None or node.id not in variables:
                raise ValueError(f"未知变量或函数: {node.id}")
    env = dict(_ALLOWED_FUNCS)
    if variables:
        env.update(variables)
    return eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, env)  # noqa: S307


# ---------------------------------------------------------------- 展示与比对
def parse_reported(text) -> tuple[float | None, str]:
    """把报告展示字符串解析成数值，返回 (数值, 单位类型)。"""
    if isinstance(text, (int, float)):
        return float(text), "plain"
    s = str(text).strip()
    unit = "plain"
    if "%" in s:
        unit = "percent"
    s = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    if s in ("", "-", "."):
        return None, unit
    try:
        return float(s), unit
    except ValueError:
        return None, unit


def display(value: float, decimals: int, unit: str) -> str:
    if unit == "percent":
        return f"{value:.{decimals}f}%"
    if unit == "currency":
        return f"¥{value:,.{decimals}f}"
    return f"{value:,.{decimals}f}" if decimals else f"{value:,.0f}"


def infer_decimals(reported) -> int:
    if isinstance(reported, (int, float)):
        return 2
    s = str(reported)
    m = re.search(r"\.(\d+)", s)
    return len(m.group(1)) if m else 0


def verify_row(row: dict) -> dict:
    rid = row.get("id", "?")
    name = row.get("name", "")
    formula = row.get("formula", "")
    reported = row.get("reported")
    decimals = row.get("decimals", infer_decimals(reported))
    unit = row.get("unit") or parse_reported(reported)[1]
    tol = row.get("tolerance")
    if tol is None:
        tol = 0.5 * (10 ** -decimals) + 1e-9 if decimals else 0.51
    out = {"id": rid, "name": name, "formula": formula,
           "reported": str(reported), "computed": "", "diff": "", "status": "warn"}
    try:
        value = safe_eval(formula, row.get("vars"))
    except Exception as exc:  # noqa: BLE001
        out.update(computed="求值失败", diff="-", status="fail",
                   error=f"{type(exc).__name__}: {exc}")
        return out

    out["computed_raw"] = value
    out["computed"] = display(value, decimals, unit)
    rep_val, _ = parse_reported(reported)
    if rep_val is None:
        out.update(diff="-", status="fail", error="报告值无法解析为数字")
        return out

    # 报告值按展示口径存的是百分数本身（如 16.1 表示 16.1%），
    # 若公式返回的是比率（0~1 之间且单位是 percent），换算成百分数再比。
    cmp_val = value * 100.0 if (unit == "percent" and -1.5 < value < 1.5) else value
    diff = cmp_val - rep_val
    out["diff_pp"] = diff
    out["diff"] = (f"{abs(diff):.{max(decimals, 2)}f}"
                   + ("pp" if unit == "percent" else ""))
    if abs(diff) <= tol:
        out["status"] = "pass"
    elif abs(diff) <= max(tol * 10, 0.05 if unit == "percent" else 0.5):
        out["status"] = "warn"
    else:
        out["status"] = "fail"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="复算报告派生指标")
    ap.add_argument("spec", help="calcs.json 路径")
    ap.add_argument("--emit-audit", action="store_true", help="输出 DATA.audit.rows 可用的 JSON")
    ap.add_argument("--strict", action="store_true", help="存在 fail 项时退出码 1")
    args = ap.parse_args()

    with open(args.spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    rows = [verify_row(r) for r in spec.get("rows", [])]

    cnt = {"pass": 0, "warn": 0, "fail": 0}
    for r in rows:
        cnt[r["status"]] = cnt.get(r["status"], 0) + 1

    if args.emit_audit:
        print(json.dumps({
            "source": spec.get("source", "scripts/verify_calcs.py 输出"),
            "note": spec.get("note", ""),
            "rows": [{k: r[k] for k in ("id", "name", "formula", "computed",
                                        "reported", "diff", "status") if k in r}
                     for r in rows],
        }, ensure_ascii=False, indent=2))
    else:
        print("=" * 96)
        print(f"{'编号':<6}{'指标':<26}{'复算值':>16}{'报告值':>16}{'偏差':>10}  结论")
        print("-" * 96)
        mark = {"pass": "✓ 一致", "warn": "△ 尾差", "fail": "✕ 不一致"}
        for r in rows:
            line = (f"{r['id']:<6}{r['name'][:24]:<26}{r['computed']:>16}"
                    f"{r['reported']:>16}{r['diff']:>10}  {mark[r['status']]}")
            print(line)
            if r.get("error"):
                print(f"      ↳ 错误: {r['error']}")
            elif r["status"] != "pass" and "computed_raw" in r:
                print(f"      ↳ 未取整原值: {r['computed_raw']!r}")
        print("-" * 96)
        print(f"合计 {len(rows)} 项：通过 {cnt['pass']} · 需关注 {cnt['warn']} · 不一致 {cnt['fail']}")
        if spec.get("note"):
            print("口径说明：" + spec["note"])
        print("=" * 96)

    if args.strict and cnt["fail"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
