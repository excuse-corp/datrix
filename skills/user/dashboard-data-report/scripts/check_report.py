#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付前自检：校验生成的报告 HTML 是否结构完整、图表可渲染、数据自洽。

用法:
    python3 scripts/check_report.py report.html
    python3 scripts/check_report.py report.html --strict   # 有问题时退出码 1

检查项:
  1. HTML 标签闭合与配对
  2. <script> 括号/花括号平衡；若环境有 node 则做真实语法检查
  3. getElementById 引用的静态 id 是否都存在
  4. 是否保留《计算结果复核记录》表（audit 区块）
  5. audit.rows 中是否存在 fail 项（存在即不合格）
  6. 是否残留 NaN / undefined / Infinity 等渲染错误
  7. 环形图/堆叠图分项之和与声明合计是否自洽（若正文写了合计）
"""
from __future__ import annotations

import html.parser
import os
import re
import shutil
import subprocess
import sys

VOID = {"meta", "br", "img", "input", "hr", "link", "source", "path",
        "circle", "line", "polyline", "stop", "rect", "polygon"}


class Checker(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"结束标签 </{tag}> 与栈顶 {self.stack[-3:]} 不匹配")


def num(text: str) -> float | None:
    s = re.sub(r"[^\d.\-]", "", str(text).replace(",", ""))
    try:
        return float(s)
    except ValueError:
        return None


def check(path: str) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    src = open(path, encoding="utf-8").read()

    # 1) 标签闭合
    c = Checker()
    c.feed(src)
    if c.stack:
        problems.append(f"未闭合标签: {c.stack}")
    if c.errors:
        problems.extend(c.errors[:5])

    # 2) JS 语法
    m = re.search(r"<script>(.*?)</script>", src, re.S)
    if not m:
        problems.append("找不到 <script> 区块，模板可能被破坏")
        return problems, notes
    js = m.group(1)
    if shutil.which("node"):
        # node --check 不接受 /dev/stdin，必须落临时文件
        tmp = path + ".__chk__.js"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(js)
            r = subprocess.run(["node", "--check", tmp], text=True, capture_output=True)
            if r.returncode != 0:
                first = [x for x in r.stderr.splitlines() if x.strip()]
                problems.append("JS 语法错误: " + (first[0][:180] if first else "未知"))
            else:
                notes.append("node --check 语法校验通过")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    else:
        # 降级：先剥离注释与字符串再数括号，避免注释里的 "1)" 造成误报
        stripped = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        stripped = re.sub(r"(?<![:'\"])//[^\n]*", "", stripped)
        stripped = re.sub(r"(['\"])(?:\\.|(?!\1)[^\\])*?\1", "", stripped)
        for a, b, name in (("{", "}", "花括号"), ("(", ")", "圆括号"), ("[", "]", "方括号")):
            d = stripped.count(a) - stripped.count(b)
            if d:
                problems.append(f"JS {name}不平衡，差值 {d}")
        notes.append("环境无 node，仅做（已剥离注释/字符串的）括号平衡检查")

    # 3) id 引用
    ids = set(re.findall(r'id="([^"]+)"', src))
    used = set(re.findall(r'getElementById\(["\']([^"\']+)["\']\)', src))
    missing = {u for u in used if u not in ids and not re.search(r'["\']' + re.escape(u) + r'["\']\s*\+', js)}
    if missing:
        problems.append(f"getElementById 引用了不存在的 id: {sorted(missing)}")

    # 4) 复核表必须存在
    if "计算结果复核记录" not in src and "auditBody" not in src:
        problems.append("缺少《计算结果复核记录》表 — 本技能强制要求")

    # 5) audit 行状态
    rows = re.findall(r'\{\s*id:\s*"([^"]+)"[^}]*?status:\s*"(\w+)"', src)
    if rows:
        fails = [i for i, s in rows if s == "fail"]
        warns = [i for i, s in rows if s == "warn"]
        notes.append(f"复核表 {len(rows)} 项：pass {sum(1 for _, s in rows if s == 'pass')} · "
                     f"warn {len(warns)} · fail {len(fails)}")
        if fails:
            problems.append(f"存在未通过复核的指标: {fails} — 禁止交付")
    else:
        notes.append("未用正则解析到 audit.rows（若为 JS 动态生成可忽略）")

    # 6) 渲染错误残留
    leaks = re.findall(r".{0,40}(?:NaN|undefined|Infinity).{0,20}", src)
    leaks = [x for x in leaks if not re.search(r"(//|/\*|\*|=== |!== |typeof)", x)]
    if leaks:
        problems.append(f"疑似渲染错误残留 {len(leaks)} 处，例如: {leaks[0][:70]}")

    # 7) 环形图分项之和 vs 声明合计
    donut = re.search(r'type:\s*"donut".*?items:\s*\[(.*?)\]', src, re.S)
    if donut:
        vals = [float(v.replace(",", "")) for v in re.findall(r"value:\s*([\d,.]+)", donut.group(1))]
        total = sum(vals)
        foot = re.search(r'type:\s*"donut".*?foot:\s*"([^"]*)"', src, re.S)
        if foot:
            declared = num(re.search(r"([\d,]+\.\d{2}|[\d,]+)", foot.group(1)).group(1)) if \
                re.search(r"[\d,]+\.\d{2}|[\d,]+", foot.group(1)) else None
            if declared and abs(declared - total) > 0.51:
                problems.append(f"环形图分项之和 {total:,.2f} 与脚注声明合计 {declared:,.2f} 不一致")
            elif declared:
                notes.append(f"环形图合计自洽：{total:,.2f}")

    # 8) 图表容器与 viewBox 生成方式
    if re.search(r'<svg[^>]*viewBox="0 0 \d+ \d+"', src):
        problems.append("检测到硬编码 viewBox — 应交由图表库按容器像素生成，否则会拉伸")

    return problems, notes


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--strict"]
    strict = "--strict" in sys.argv
    if not args:
        print("用法: python3 check_report.py report.html [--strict]")
        return 2
    problems, notes = check(args[0])
    print("=" * 78)
    for n in notes:
        print("  · " + n)
    if problems:
        print("-" * 78)
        for p in problems:
            print("  ✕ " + p)
    else:
        print("  ✓ 全部检查通过")
    print("=" * 78)
    return 1 if (problems and strict) else 0


if __name__ == "__main__":
    sys.exit(main())
