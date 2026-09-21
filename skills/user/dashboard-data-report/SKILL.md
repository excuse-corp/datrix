---
name: dashboard-data-report
description: Generate single-file HTML data analysis reports in a warm dashboard style (cream background, orange primary, sage/amber/teal accents) with inline SVG charts — line, area, grouped bar, stacked bar, horizontal bar, donut. Every derived number is independently recomputed by scripts/verify_calcs.py and shown in a mandatory "数据核对" appendix table.
---

# Dashboard Data Report

生成单文件 HTML 数据分析报告，视觉风格对齐「暖米底 + 橙色主视觉 + 多色状态标签」的经营看板。核心约束：**所有派生数值必须经脚本独立复算**，不允许模型心算。

## Goal

产出一个自包含 HTML（无外部 CSS/JS 依赖），包含 KPI 卡、主折线图、分组卡片、多种图表，以及**强制存在**的《计算结果复核记录》表。

## Core Principles (MUST FOLLOW)

### 1. 数据准确性优先 — 计算必须复算

**禁止把模型心算的结果写进报告。** 任何非直接抄自来源的数值（占比、同比、环比、增速、均值、中位数、合计、差值、倍数、CAGR、达成率、渗透率）都必须走脚本复算流程：

1. **拆出派生指标清单** — 列出报告中每一个将要展示的派生数值，写进 `calcs.json`。
2. **用公式表达，不要写死结果** — `formula` 字段写可求值的表达式，例如 `pct(43013.36, 121076.65)`、`delta(119076.65, 102564.00)`、`mean([96, 91, 88, 84, 72])`。
3. **运行脚本复算**：
   ```bash
   python3 <skill-root>/scripts/verify_calcs.py calcs.json --strict
   ```
4. **按退出码决策** — 退出码 `1` 表示存在不一致项：**必须先修正数据或修正报告展示值，禁止带着 fail 项交付**。
5. **把结果粘进报告** — 用 `--emit-audit` 直接得到可粘贴的 `DATA.audit.rows` JSON。
6. **口径披露** — 在 `audit.note` 写明四舍五入策略、单位、时间口径；因取整导致的尾差要说明。

脚本支持的公式函数：`sum` `mean`/`avg` `median` `min` `max` `abs` `round` `sqrt` `log` `log10` `pow` `pct(部分,总体)` `delta(本期,上期)` `cagr(期末,期初,年数)`。千分位逗号会自动剥离；`vars` 字段可注入变量。

**脚本是白名单沙箱**，已验证会拒绝 `__import__`、`open()`、属性访问等任何越界表达式。不要因为"公式复杂"就绕过脚本改用口算。

### 2. 严禁编造数据

- 图表里的每个数值都要能追溯到来源（附件、用户提供的表格、检索结果）。
- 缺数据时留空或标注「待核实」，**不要用看起来合理的数字填充**。
- 若用户只给了截图/样式参考而没给数据，必须先用 `request_user_input` 索取数据，或明确告知"当前为示例数据，需替换"。

### 3. 视觉风格必须对齐参考

固定使用模板的设计令牌，不要自创配色：

| 用途 | 色值 |
|---|---|
| 页面底色 | `#F4F0E7` 暖米 |
| 卡片 | `#FFFFFF` + 边框 `#E7E2D6` |
| 主强调 / 主图 | `#D67040` 橙 |
| 次系列（对比周期） | `#EEAB64` 暖金 |
| 正向 / 已完成 | `#6F8D7F` 鼠尾草绿、`#2F7A57` |
| 预警 / 进行中 | `#D9A62C` 琥珀 |
| 风险 / 延期 | `#B4483C` 砖红 |
| 中性系列 | `#3E7C8C` 青蓝、`#3A6488` 靛蓝 |
| 主操作按钮 / 正文 | `#1D322B` 深墨绿 |

多系列图表**必须用不同颜色区分**，不要全部用橙色。

## Workflow

### Phase 1 — 收集数据

1. 附件用 `parse_attachment`（`content_mode:"full"`）逐个解析；表格类优先 `html-excel-skill`。
2. 用户给的链接用 `fetch_url`。
3. 需要公开数据时先读 `anysearch` 技能再检索。
4. 把**原始数值**（不是派生结果）整理进 `research_notes.md`，标注来源与时间口径。
5. 数据不足时调用 `request_user_input` 索取，不要编造。

### Phase 2 — 大纲与图表选型（需用户确认）

调用 `request_user_input`（`kind:"choice"`，附 `workflow_id`、`phase:"outline"`）确认：

- 报告标题 / 副标题 / 主操作按钮文案
- 分几个区块、每块放哪种图表
- KPI 主指标是哪个

图表选型见 `references/chart-config.md`。可用类型：`line` `multiLine` `groupedBar` `bar` `stackedBar` `hbar` `donut`。

选型经验：
- 时间趋势 → `line` / `multiLine`
- 类别对比 → `groupedBar` / `bar`
- 构成随时间变化 → `stackedBar`
- 排名 / 达成率（配目标线）→ `hbar`
- 占比（≤6 项）→ `donut`

### Phase 3 — 复算（硬门禁）

1. 写 `calcs.json`（结构见 `templates/calcs.example.json`）。
2. 跑 `python3 <skill-root>/scripts/verify_calcs.py calcs.json --strict`。
3. **退出码非 0 就回到第 1 步修正**，不得跳过。
4. 跑 `--emit-audit` 拿到 `audit.rows`。

### Phase 4 — 生成 HTML

1. `read_file` 读取 `templates/dashboard_template.html`。
2. **只替换 `const DATA = {...}` 这一个对象**，不要改图表库和 CSS。
3. 区块分配：`gridA` 三栏、`gridB` 宽+窄两栏、`gridC` 等宽两栏；不需要的区块设为 `[]`。
4. `audit.rows` 用 Phase 3 的 `--emit-audit` 输出，**不要手填复算值**。
5. 写入当前回合输出目录。

### Phase 5 — 自检交付

1. 用 Python 校验标签闭合、JS 括号平衡、`getElementById` 引用的 id 全部存在。
2. 核对 `DATA` 里展示的每个派生数值都能在 `audit.rows` 找到对应行。
3. 检查环形图/堆叠图分项之和与声明的合计一致。
4. 如实告知用户：是否做过真实浏览器渲染验证（环境无浏览器时必须声明未做像素级比对）。

## Chart Config

所有图表配置集中在 `DATA` 对象，详见 `references/chart-config.md`。要点：

- 图表按**容器真实像素**生成 `viewBox`，并监听 `resize` 重绘，因此不会拉伸变形。不要在图表里手写固定 `viewBox`。
- `yFmt` 取 `"k" | "k1" | "currency" | "plain"`。
- 虚线系列用 `dash:"6 3"`；面积用 `area:true`。
- `insight` 字段会在图表下方渲染一条结论条，建议每个图表都写一句，但内容必须来自已核对的数据。

## Constraints

- 单文件 HTML，除系统字体外无外部依赖。
- 必须保留《计算结果复核记录》表；无派生计算时也要保留该表并写明"本报告无自行计算指标"。
- 存在 `fail` 项时禁止交付。
- 不编造数据、不编造来源、不谎称已做视觉验证。
- 最终文件写入当前回合输出目录。

## Tool Guidance

| 步骤 | 工具 | 说明 |
|---|---|---|
| 解析附件 | `parse_attachment` | `content_mode:"full"` |
| 读表格 | `html-excel-skill` | xlsx/csv 数据源 |
| 抓链接 | `fetch_url` | 仅用户给定或搜索已返回的 URL |
| 检索 | `anysearch` | 需公开数据时先读其 SKILL.md |
| 大纲确认 | `request_user_input` | `kind:"choice"` + `phase:"outline"` |
| 复算 | `terminal` | `python3 scripts/verify_calcs.py calcs.json --strict` |
| 取核对表 | `terminal` | 同上 + `--emit-audit` |
| 读模板 | `read_file` | `templates/dashboard_template.html` |
| 写产物 | `write_file` | 输出到当前回合输出目录 |
| 自检 | `terminal` | HTML 解析器 + 括号平衡校验 |

`verify_calcs.py` 只用标准库，无需 `.venv` 或 `pip install`。
