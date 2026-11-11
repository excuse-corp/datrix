# 信息化项目数据流程与经费年份改造说明

> 核查日期：2026-10-26
> 用途：说明信息化项目数据从 Ecology 上游到 DataMan 问数结果的流转过程，并列出将经费年份编码转换为真实年份所需的改动。
> 安全说明：本文不记录数据库主机地址、账号或密码。连接凭证应放在受控的密钥配置中，不应写入仓库。

### 实施状态（2026-10-26）

- 已准备上游视图 SQL，追加经费类型原始编码和字典翻译后的经费年份；尚未在 Ecology SQL Server 上执行该视图变更。
- 已给本地 PostgreSQL 的 `sync.pcm_project_info` 增加 `funding_type_code`、`funding_year`，并扩展 reporting 视图；迁移文件为 `docker/dataman-postgres/migrations/002-add-project-funding-code-and-year.sql`。
- 已更新初始化脚本、字段映射和 AskData 语义文档。
- 尚需在数据交换平台配置两列映射并重新同步，再刷新/重建 AskData Scene Snapshot。上游视图和同步平台未完成前，新加的 PostgreSQL 列会保持空值。

## 一、端到端数据流程

```text
Ecology SQL Server
  ├─ dbo.uf_xmk：项目主数据
  │    ├─ id       → 项目ID
  │    ├─ jflx     → 经费类型代码
  │    └─ jflxnf   → 经费年份选项代码
  └─ dbo.workflow_selectitem：选择项字典
       ├─ fieldid=10657：经费类型选项
       └─ fieldid=12163：当前项目表单的经费年份选项
             ↓
dbo.vw_xmk_project_contract_report（上游报表视图）
  ├─ 经费类型名称：jflx 经 fieldid=10657 字典翻译
  ├─ 经费类型编码：jflx 原始值
  ├─ 经费年份编码：jflxnf 原始值（既有列）
  └─ 经费年份：jflxnf 经 fieldid=12163 字典翻译（新列，待上游发布）
             ↓ 第三方同步平台（当前可观察到 JDBC 写入）
dataman_data PostgreSQL
  └─ sync.pcm_project_info（同步落地表）
             ↓
  └─ reporting.vw_information_project_contract_report（只读查询视图）
             ↓
DataMan AskData
  ├─ 数据源：dataman_data
  ├─ Scene：information_project_contract_report
  └─ Snapshot / 语义配置 / 只读 SQL 查询
             ↓
问数 API、Agent 与前端展示
```

### 各环节职责

| 环节 | 对象 | 职责 |
|---|---|---|
| 上游业务数据 | `dbo.uf_xmk` | 一行代表一个项目；`id` 是项目 ID，`jflx` 和 `jflxnf` 保存选择项代码。 |
| 上游字典 | `dbo.workflow_selectitem` | 用 `selectvalue` 保存选项代码，用 `selectname` 保存用户看到的文字。经费类型字段 ID 是 `10657`；当前项目表单的年份字段 ID 是 `12163`。 |
| 上游报表视图 | `dbo.vw_xmk_project_contract_report` | 把项目字段组织成同步接口；更新后的定义同时输出经费类型编码/名称、经费年份编码/名称，实际发布仍待完成。 |
| 数据同步 | 第三方同步平台 | 从上游视图读取数据，再写入 PostgreSQL 的同步表。DataMan 本身不负责这一步。 |
| 本地落地表 | `sync.pcm_project_info` | 保存同步后的项目数据，是同步平台的写入目标。 |
| 本地查询视图 | `reporting.vw_information_project_contract_report` | 为 DataMan 提供受控、只读的项目查询字段，并进行必要的口径整理。 |
| 问数场景 | AskData Scene / Snapshot | 限定可查询视图、业务语义和权限；Agent 根据用户问题查询只读视图并返回结果。 |

## 二、经费年份字段的正确含义

| 上游字段 | 含义 | 当前值形态 |
|---|---|---|
| `uf_xmk.jflx` | 经费类型代码 | 整数选项值；通过 `workflow_selectitem` 的 `fieldid=10657` 翻译成“国拨改善”等名称。 |
| `uf_xmk.jflxnf` | 经费年份选项代码 | 整数选项值；通过当前项目表单年份字段 `workflow_selectitem.fieldid=12163` 的 `selectvalue` 查到 `selectname` 年份。 |

上游表单配置显示，经费类型字段 `10657` 的子字段是年份字段 `12163`，所以在业务表单上，经费年份选项受经费类型联动控制。年份代码本身不是年份，也不能用代码大小或项目日期推算年份。

上游原视图只输出经费类型名称和 `jflxnf` 编码，没有单独输出经费类型原码或年份名称。更新定义在 `configs/alter_ecology_project_contract_report_view.sql`：保留既有输出，并在末尾追加 `uf_xmk.jflx` 原码及年份字典 `selectname`；应用到上游数据库后才会生效。

### 本次核实到的实际映射

以下是当前项目记录中出现的编码，经上游年份字典 `fieldid=12163` 确认：

| 经费类型 | 年份编码 | 年份 |
|---|---:|---:|
| 国拨改善 | 3、4、5、19、20、21 | 2021、2022、2023、2024、2025、2026 |
| 银校合作 | 1、6、48 | 2021、2022、2026 |
| 网络安全与信息化常年维修维保项目 | 8、9、10、11、12、13、14 | 2020、2021、2022、2023、2024、2025、2026 |
| 信息化建设经费 | 43、44 | 2025、2026 |
| 财务预算一体化系统专网采购 | 25、26 | 2025、2026 |
| 网络安全加固服务 | 67 | 2026 |

验证时，当前项目表单年份选项表中每个 `selectvalue` 唯一对应一个 `selectname`，上述项目记录中的年份编码均能查到对应年份。其他表单版本也有相似的年份字典字段 ID；维护 SQL 时应使用与项目视图字段配置相对应的 `12163`，不要仅凭字段名称选取其他表单的 ID。

## 三、字段变更和待完成事项

### 已实施的兼容性变更

1. **上游 SQL Server 报表视图：定义已更新，部署待完成**
   - 目标字段为 `[经费类型编码] = uf_xmk.jflx` 和 `[经费年份] = 年份字典 selectname`。
   - 保留既有 `[经费类型]` 名称和 `[经费类型年份]` 编码，不重命名或删除。
   - 年份字典按 `workflow_selectitem.isbill=1`、`fieldid=12163`、`selectvalue=x.jflxnf` 关联；经费类型名称仍由 `fieldid=10657` 翻译。
   - 由有权限的上游数据库维护人员执行 `configs/alter_ecology_project_contract_report_view.sql`，并核对结果行数、重复和年份匹配情况。

   逻辑示意：

   ```sql
   -- SELECT 列中保留原编码，并新增真实年份
   x.jflxnf AS [经费类型年份],
   si_jflxnf.selectname AS [经费年份]

   -- FROM / JOIN 中增加
   LEFT JOIN dbo.workflow_selectitem AS si_jflxnf
       ON si_jflxnf.isbill = 1
      AND si_jflxnf.fieldid = 12163
      AND si_jflxnf.selectvalue = x.jflxnf
   ```

2. **第三方同步平台字段映射：待配置**
   - `[经费类型编码]` → `funding_type_code`；`[经费年份]` → `funding_year`。
   - 保留 `[经费类型年份]` → `funding_type_year`，用于编码追溯；业务年份问题使用 `funding_year`。
   - 优先按列名映射；若平台按列序号同步，核对追加列顺序后再启用任务。

3. **PostgreSQL 同步表与查询视图：本地已迁移**
   - `sync.pcm_project_info` 新增 `funding_type_code integer` 和 `funding_year varchar(32)`；既有字段不删除、不重命名。
   - reporting 视图在原有输出末尾追加这两列，保留既有列顺序及 `line_department` 派生规则。
   - 初始化定义在 `docker/dataman-postgres/init/001-create-information-project-schema.sh`；已运行数据库的可重复迁移在 `docker/dataman-postgres/migrations/002-add-project-funding-code-and-year.sql`。

4. **AskData 语义和场景 Snapshot：语义已更新，Snapshot 待刷新**
   - 已更新字段定义、同步语义和 Scene 语义。
   - 明确 `funding_year` 是年份筛选、分组和统计字段；`funding_type_year` 是旧名称下的原始选项编码，不用于回答年份问题。
   - 上游同步完成后重新读取视图结构、构建 Snapshot 并验收。

### 外部待完成事项

- 在 Ecology SQL Server 发布更新后的源视图定义。
- 在数据交换平台配置两列新增映射并执行同步。
- 同步后刷新/重建 AskData Scene Snapshot，并验证新字段进入场景。

## 四、实施顺序与验收

后续按以下顺序完成剩余部署：

1. 在上游 SQL Server 更新报表视图，确认原代码、新经费类型编码和年份名称正确返回且项目行数未增加。
2. 更新同步平台字段映射并同步数据；本地 PostgreSQL 结构已准备完成。
3. 验证 `dataman_reader` 可读 reporting 视图及新字段。
4. 刷新/重建 AskData Scene Snapshot 并验收。
5. 用问数分别验证“按经费年份筛选”“按经费类型和年份分组”以及空年份记录的处理。

验收至少确认：

- 上游年份字典可将项目记录的 `jflxnf` 翻译为 `2020` 至 `2026` 等真实年份文本。
- PostgreSQL 中 `funding_year` 保存真实年份；`funding_type_year` 保留原始代码，不因同步被覆盖。
- SQL 查询可用 `funding_year = '2025'` 过滤，并按 `funding_type, funding_year` 分组。
- 空编码或未匹配字典的记录保持为空，不根据项目中标日期或项目名称猜补年份。
- DataMan 场景 Snapshot 已暴露 `funding_year`，Agent 不再把类似 `0000000003` 的值当成真实年份。

## 五、相关文件

- 上游视图 SQL：`configs/alter_ecology_project_contract_report_view.sql`
- 上游到同步表字段映射：`dataman1111/vw_xmk_to_sync_pcm_project_info_mapping.md`
- PostgreSQL 已运行实例迁移：`docker/dataman-postgres/migrations/002-add-project-funding-code-and-year.sql`
- PostgreSQL 初始化结构与 reporting 视图：`docker/dataman-postgres/init/001-create-information-project-schema.sh`
- PostgreSQL 同步落地字段说明：`dataman1111/sync_pcm_project_info_fields.md`
- AskData 场景语义：`dataman1111/scenes/information_project_contract_report.semantic.md`
- AskData 数据源配置示例：`configs/ask-data.example.toml`
- 同步表和查询视图维护指南：`configs/DATAMAN_DATA_SYNC_GUIDE.md`
- AskData 配置说明：`configs/ASK_DATA_CONFIG.md`
