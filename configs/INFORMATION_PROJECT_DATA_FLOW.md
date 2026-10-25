# 信息化项目数据流程与经费年份改造说明

> 核查日期：2026-10-12
> 用途：说明信息化项目数据从 Ecology 上游到 DataMan 问数结果的流转过程，并列出将经费年份编码转换为真实年份所需的改动。
> 安全说明：本文不记录数据库主机地址、账号或密码。连接凭证应放在受控的密钥配置中，不应写入仓库。

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
  ├─ 已将 jflx 翻译成经费类型名称
  └─ 当前将 jflxnf 原样输出为编码，尚未输出年份名称
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
| 上游报表视图 | `dbo.vw_xmk_project_contract_report` | 把项目字段组织成同步接口。目前经费类型已翻译，年份字段仍是代码。 |
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

上游视图当前的相关逻辑在 `dataman1111/create_vw_xmk_project_contract_report.sql`：经费类型通过 `fieldid=10657` 翻译；年份列直接选取 `x.jflxnf`，没有关联年份选项名称。这是年份在 DataMan 中显示为代码的根因。

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

## 三、需要修改哪些部分

### 必须修改：让真实年份进入 DataMan

1. **上游 SQL Server 报表视图**
   - 修改 `dataman1111/create_vw_xmk_project_contract_report.sql` 对应的上游视图定义。
   - 保留原始年份代码列以便追溯，新增清晰命名的年份列，例如 `[经费年份]`。
   - 新增年份字典关联：`workflow_selectitem.isbill=1`、`fieldid=12163`、`selectvalue=x.jflxnf`，输出 `selectname`。
   - 不要将年份编码直接改名成真实年份；编码和翻译值应分列保存。

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

2. **第三方同步平台字段映射**
   - 将新增的 `[经费年份]` 映射到 PostgreSQL 新字段 `funding_year`。
   - 保留原代码字段 `funding_type_year`，用于排错和追溯。
   - 确认同步任务执行全量或增量更新，并检查 `funding_year` 是否为空或出现非四位年份值。

3. **PostgreSQL 同步表与查询视图**
   - 在 `sync.pcm_project_info` 增加 `funding_year varchar(4)`。
   - 在 `reporting.vw_information_project_contract_report` 输出 `source.funding_year`；对现有 PostgreSQL 视图做 `CREATE OR REPLACE VIEW` 时，将新列追加在现有列清单末尾，保持原有列名和顺序不变。
   - 同时更新初始化脚本 `docker/dataman-postgres/init/001-create-information-project-schema.sh`。
   - 对已运行的数据库单独执行迁移；修改初始化脚本不会自动更新已有数据卷。

   运行库迁移逻辑示意：

   ```sql
   ALTER TABLE sync.pcm_project_info
     ADD COLUMN IF NOT EXISTS funding_year varchar(4);

   -- 在完整的现有 SELECT 列清单最后追加：
   source.funding_year
   ```

   上面只展示需要追加的列，不是可单独执行的 SQL。实际执行 `CREATE OR REPLACE VIEW` 时必须保留现有视图的全部列、顺序和既有 `line_department` 派生逻辑，并把 `funding_year` 追加到列清单末尾。

4. **AskData 语义和场景 Snapshot**
   - 更新 `dataman1111/sync_pcm_project_info_fields.md`、`dataman1111/sync_pcm_project_info_semantic_layer.md` 和 `dataman1111/scenes/information_project_contract_report.semantic.md`。
   - 明确 `funding_year` 是可用于年份筛选、分组和统计的真实年份；`funding_type_year` 只是原始选项编码，默认不用于回答年份问题。
   - 更新场景后重新校验/构建并激活 Scene，使新的 Snapshot 包含 `funding_year`。

### 建议同步修改：保持文档与运行现状一致

- 更新 `dataman1111/vw_xmk_to_sync_pcm_project_info_mapping.md`，记录新增源列到 `funding_year` 的映射。
- 更新 `configs/DATAMAN_DATA_SYNC_GUIDE.md`，说明本场景采用“上游 SQL Server → 第三方同步 → PostgreSQL”的链路。
- 更新 `configs/ASK_DATA_CONFIG.md` 中仍称 DataMan 直接连接 SQL Server、且描述本地表为空的旧内容；当前 AskData 查询配置绑定的是 PostgreSQL `dataman_data`，本次检查时同步表和查询视图各有 384 条记录。

## 四、实施顺序与验收

建议按以下顺序实施，避免下游先改好但拿不到年份：

1. 在上游 SQL Server 更新报表视图，确认原代码与新增年份列同时正确返回。
2. 更新同步平台映射和 PostgreSQL 表结构，运行一次全量/增量同步。
3. 更新 reporting 视图并验证 DataMan 只读账号仍可查询。
4. 更新语义文档，重建并激活 AskData Scene Snapshot。
5. 用问数分别验证“按经费年份筛选”“按经费类型和年份分组”以及空年份记录的处理。

验收至少确认：

- 上游年份字典可将项目记录的 `jflxnf` 翻译为 `2020` 至 `2026` 等真实年份文本。
- PostgreSQL 中 `funding_year` 保存真实年份；`funding_type_year` 保留原始代码，不因同步被覆盖。
- SQL 查询可用 `funding_year = '2025'` 过滤，并按 `funding_type, funding_year` 分组。
- 空编码或未匹配字典的记录保持为空，不根据项目中标日期或项目名称猜补年份。
- DataMan 场景 Snapshot 已暴露 `funding_year`，Agent 不再把类似 `0000000003` 的值当成真实年份。

## 五、相关文件

- 上游视图 SQL：`dataman1111/create_vw_xmk_project_contract_report.sql`
- 上游到同步表字段映射：`dataman1111/vw_xmk_to_sync_pcm_project_info_mapping.md`
- PostgreSQL 初始化结构与 reporting 视图：`docker/dataman-postgres/init/001-create-information-project-schema.sh`
- PostgreSQL 同步落地字段说明：`dataman1111/sync_pcm_project_info_fields.md`
- AskData 场景语义：`dataman1111/scenes/information_project_contract_report.semantic.md`
- AskData 数据源配置示例：`configs/ask-data.example.toml`
- 同步表和查询视图维护指南：`configs/DATAMAN_DATA_SYNC_GUIDE.md`
- AskData 配置说明：`configs/ASK_DATA_CONFIG.md`
