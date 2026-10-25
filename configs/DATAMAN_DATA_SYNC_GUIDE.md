# DataMan 数据同步与 AskData 接入指南

本文档合并了数据同步通用设计规范与 PostgreSQL / AskData 接入操作。适用于新增业务场景、数据源或数据集；每个场景仍需单独确认上游、字段、业务口径和验收标准。文中的 `example_table`、`public` 等仅为操作示例，实施时应按同步平台约束和场景数据契约调整。

## 1. 目标与基本原则

- 上游系统是业务事实来源；同步平台负责抽取、转换和传输；DataMan 负责受控查询、语义解释与结果展示。
- 先明确数据粒度、主键、字段口径、刷新频率、删除规则和数据质量标准，再建表和配置同步。
- 同步平台只写基础同步表；AskData 只读 `reporting` 查询视图，不直接读取上游库或同步表。
- 保留稳定的上游业务 ID；代码型字段优先保留原代码和经确认的业务名称。
- 业务代码必须按正确的字典范围或经业务确认的映射翻译，不得根据名称、日期或编号规律猜测。
- 如实保留空值；除非有明确业务规则，不将空值自动改成 `0`、`未知` 等含义。
- 上游和 PostgreSQL 账号均采用最小权限；凭据不得写入仓库、SQL 文件或命令历史。
- 运行中数据库和新环境初始化脚本保持一致；结构、同步配置和语义变更要有验证及回滚方案。

## 2. 标准数据流

```text
上游业务系统（业务表、视图、API、文件及字典）
  → 只读抽取
数据同步平台 / ETL / CDC（字段映射、类型转换、重试、日志）
  → 写入
PostgreSQL 同步基础表（建议 sync schema；受第三方限制时可使用其指定 schema）
  → 只读整理
reporting.vw_<domain>_<entity>（字段白名单、业务口径、必要的字典关联）
  → 绑定
AskData 数据源 / Scene / Snapshot / 语义层
  → 权限校验和受控 SQL
AskData API / Agent / 前端结果
```

| 层 | 主要职责 | 边界 |
|---|---|---|
| 上游系统 | 保存业务事实、字典和流程数据。 | 不依赖 DataMan 的问数语义。 |
| 同步平台 | 按数据契约抽取、映射和写入，处理幂等、重试及同步状态。 | 不负责 AskData 的语义解释。 |
| 同步基础表 | 保存可追溯的上游同步数据。 | 不直接作为 AskData 的查询对象。 |
| `reporting` 视图 | 暴露稳定、只读、最小化的业务查询接口。 | 不做无依据的猜测性数据修正。 |
| AskData Scene | 绑定授权视图，定义业务词汇、指标口径和场景范围。 | 不替代同步平台或上游系统的权限控制。 |

信息化项目的实际链路可参照 `configs/INFORMATION_PROJECT_DATA_FLOW.md`；其他场景替换各自的数据源和业务规则，但保持上述职责边界。

## 3. 接入前：建立数据契约

建表前由业务和数据负责人确认下列项目；关键口径未确认时，不要直接上线。

| 项目 | 需要确认的内容 |
|---|---|
| 负责人和用途 | 业务负责人、验收人、要回答的问题及明确不支持的范围。 |
| 上游来源 | 系统、数据库/schema、表/视图/API/文件及维护责任人。 |
| 数据粒度 | 一行代表什么，例如一个项目、一笔付款或一条事件。 |
| 主键 | 稳定唯一的上游 ID；若无单列主键，确认稳定的组合键。 |
| 时间语义 | 创建、业务发生、更新时间、同步时间分别代表什么，以及使用的时区。 |
| 刷新要求 | 全量/增量/CDC、刷新频率、允许延迟和恢复要求。 |
| 更新与删除 | 上游更新、撤销、软删除或物理删除如何反映到目标表。 |
| 代码与字典 | 字典来源、所属字段/范围、联动关系、有效期及变更规则。 |
| 安全分类 | 敏感字段、必要的脱敏/排除措施、用户和组织访问范围。 |
| 质量标准 | 行数、唯一性、必填、值域、映射覆盖率及关键指标核对方法。 |

每张事实表要有一句话粒度定义。展示名称不能代替主键；一对多关系须明确聚合或拆表规则，避免连接后记录数和金额被重复放大。

## 4. 选择同步模式

| 模式 | 适用情况 | 必须明确的处理方式 |
|---|---|---|
| 全量替换 | 数据量小且可定期完整重取。 | 在临时表或事务中装载、校验后再切换；不要先清空正式表再导入。 |
| 全量 Upsert | 可重复传输完整数据，按主键覆盖当前状态。 | 主键唯一且过程幂等；另行处理上游已删除记录。 |
| 增量 Upsert | 有可靠更新时间、递增序号或水位字段。 | 防漏取数、按主键去重、记录成功水位，失败可重放。 |
| CDC / 变更日志 | 数据量大或需准确跟踪更新和删除。 | 明确顺序、断点恢复、删除事件和日志保留策略。 |
| 追加式事件 | 每行是不可变事件或流水。 | 使用事件 ID 去重；不要误当作只保留当前状态的表。 |

不能假设简单使用 `updated_at > 上次时间` 就一定可靠；需评估相同时间戳、时钟偏差、迟到更新、时区、删除事件和任务中断。

## 5. 字段映射与类型规范

每个场景维护字段映射表，至少记录上游对象/字段、目标字段、目标类型、中文业务名、转换规则、空值规则和敏感级别。例如：

| 上游对象/字段 | 目标字段 | 类型 | 业务名 | 转换/空值规则 |
|---|---|---|---|---|
| `source_table.source_id` | `source_id` | `bigint` | 上游记录 ID | 原样保留，不允许为空 |

- 使用 PostgreSQL 原生类型；金额用明确精度的 `numeric(p,s)`，日期用 `date`，时间按语义选择 `timestamp` 或 `timestamptz`。
- 不要把无时区时间擅自当作 UTC；区分业务时间与同步时间。
- 带前导零的编号和代码使用文本类型；需要精确计算的金额不要使用浮点数。
- Oracle / SQL Server 等源类型需映射为 PostgreSQL 支持的类型，例如 `VARCHAR2(n)` 改为 `varchar(n)`。
- 字符集或编码转换要用中文样本验证；乱码应先修复读取/解码方式，不要当作正常数据入库。
- 字典关联必须确认具体字段和字典范围；联动选项要同时匹配正确的父字段。优先在可审计的一层完成翻译，并保留原代码；无正式映射时保留代码或标记待确认，不得推断。

## 6. PostgreSQL 命名、分层与权限

- 新场景在同步平台允许时优先将基础表放入 `sync` schema，使用 `sync.<domain>_<entity>`；若第三方平台强制使用 `public` 或既有表名，则遵守其要求并收紧权限。
- 查询视图使用 `reporting.vw_<domain>_<entity>`；AskData 只绑定明确的视图，不绑定整个 schema 或同步表。
- 主键约束使用 `pk_<table_name>`；字段使用小写蛇形英文名，中文业务含义写入表/列注释和字段映射文档。
- 视图只暴露场景所需字段，默认排除密码、令牌、身份证号及无关联系方式等敏感字段；计算和业务口径需有说明。
- 对已有视图使用 `CREATE OR REPLACE VIEW` 增加列时，保持旧列名和顺序并将新列追加到末尾；需要改变旧列结构时，使用显式迁移。

权限边界：上游账号只读所需源对象；`dataman_sync` 或专用同步账号只写目标基础表；`dataman_reader` 只读指定 `reporting` 视图；管理账号仅用于人工建表、迁移和授权。不要用上游管理员账号或 PostgreSQL 超级用户跑同步，也不要仅依赖 Agent 提示词隐藏敏感字段。凭据应通过受控 Secret 或未跟踪的本地环境配置管理，并制定轮换流程。

## 7. PostgreSQL 与 AskData 接入操作

### 1. 确认数据库运行状态

```bash
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | grep dataman-postgres
docker exec dataman-postgres pg_isready -U dataman_admin -d dataman_data
```

### 2. 编写基础同步表 DDL

示例：

```sql
CREATE TABLE public.example_table (
  id integer NOT NULL,
  name varchar(200) NOT NULL,
  status varchar(50),
  imported_at timestamp without time zone,
  CONSTRAINT pk_example_table PRIMARY KEY (id)
);

COMMENT ON TABLE public.example_table IS '示例同步表(来源:第三方系统)';
COMMENT ON COLUMN public.example_table.id IS '主键ID';
COMMENT ON COLUMN public.example_table.name IS '名称';
COMMENT ON COLUMN public.example_table.status IS '状态';
COMMENT ON COLUMN public.example_table.imported_at IS '导入时间';
```

注意事项：

- PostgreSQL 不支持 `VARCHAR2`，使用 `varchar`。
- 展示型容量、CPU、内存等字段如果包含单位，可以先按 `varchar` 保存原值。
- 金额、数量、比例等需要计算的字段应使用 `numeric`、`integer`、`bigint` 等数值类型。
- 时间字段推荐使用 `timestamp without time zone`，除非明确需要时区语义。
- 只有确定稳定唯一的字段才设为主键。

### 3. 创建 AskData 查询视图

示例：

```sql
CREATE OR REPLACE VIEW reporting.vw_example_table AS
SELECT
  source.id,
  source.name,
  source.status,
  source.imported_at
FROM public.example_table AS source;

COMMENT ON VIEW reporting.vw_example_table IS
  'DataMan 示例同步表场景唯一允许读取的视图。';
COMMENT ON COLUMN reporting.vw_example_table.id IS '主键ID';
COMMENT ON COLUMN reporting.vw_example_table.name IS '名称';
COMMENT ON COLUMN reporting.vw_example_table.status IS '状态';
COMMENT ON COLUMN reporting.vw_example_table.imported_at IS '导入时间';
```

视图可以做必要的只读整理，例如：

- 字段重命名或统一字段顺序。
- 部门、状态、枚举等业务口径归并。
- 隐藏不应给 AskData 查询的技术字段或敏感字段。
- 保留原始展示值，避免在没有明确规则时做单位换算。

### 4. 授权同步账号和查询账号

示例：

```sql
GRANT USAGE ON SCHEMA public TO dataman_sync;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
  public.example_table TO dataman_sync;

GRANT USAGE ON SCHEMA reporting TO dataman_reader;
GRANT SELECT ON TABLE reporting.vw_example_table TO dataman_reader;
```

如果全量替换方案明确需要 `TRUNCATE`，再单独授予同步账号该权限。不要给 `dataman_reader` 授予基础同步表权限，除非有明确的临时排障需求；AskData 应通过 `reporting` 视图查询。

### 5. 在运行中数据库执行变更

将 DDL 保存为临时 SQL 文件或直接通过 stdin 执行：

```bash
docker exec -i dataman-postgres psql \
  -U dataman_admin \
  -d dataman_data \
  -v ON_ERROR_STOP=1 <<'SQL'
-- 在这里粘贴 CREATE TABLE / COMMENT / CREATE VIEW / GRANT 语句
SQL
```

执行前如果对象可能已存在，先检查：

```bash
docker exec dataman-postgres psql -U dataman_admin -d dataman_data -tAc \
  "SELECT to_regclass('public.example_table'), to_regclass('reporting.vw_example_table');"
```

### 6. 同步更新 Docker 初始化脚本

把同样的 DDL 追加到：

```text
docker/dataman-postgres/init/001-create-information-project-schema.sh
```

更新内容应包括：

- `CREATE TABLE`。
- 表注释和字段注释。
- `CREATE VIEW`。
- 视图注释和字段注释。
- `dataman_sync` 与 `dataman_reader` 授权。

该脚本只在 PostgreSQL 数据卷首次初始化时自动执行；更新脚本不会自动修改已经运行的数据库。因此运行库和初始化脚本都要改。

### 7. 校验对象和权限

检查表和视图是否存在：

```bash
docker exec dataman-postgres psql -U dataman_admin -d dataman_data -P pager=off -c \
  "SELECT table_schema, table_name, table_type
     FROM information_schema.tables
    WHERE (table_schema, table_name) IN (('public','example_table'),('reporting','vw_example_table'))
    ORDER BY table_schema, table_name;"
```

检查字段：

```bash
docker exec dataman-postgres psql -U dataman_admin -d dataman_data -P pager=off -c \
  "SELECT ordinal_position, column_name, data_type, character_maximum_length, is_nullable
     FROM information_schema.columns
    WHERE table_schema='reporting' AND table_name='vw_example_table'
    ORDER BY ordinal_position;"
```

检查权限：

```bash
docker exec dataman-postgres psql -U dataman_admin -d dataman_data -P pager=off -c \
  "SELECT grantee, table_schema, table_name, privilege_type
     FROM information_schema.table_privileges
    WHERE table_schema IN ('public','reporting')
      AND table_name IN ('example_table','vw_example_table')
      AND grantee IN ('dataman_sync','dataman_reader')
    ORDER BY table_schema, table_name, grantee, privilege_type;"
```

确认 `dataman_reader` 可以查询视图：

```bash
docker exec dataman-postgres psql -U dataman_reader -d dataman_data -P pager=off -c \
  "SELECT count(*) FROM reporting.vw_example_table;"
```

确认 `dataman_reader` 不能直接查询基础表：

```bash
docker exec dataman-postgres psql -U dataman_reader -d dataman_data -P pager=off -v ON_ERROR_STOP=0 -c \
  "SELECT count(*) FROM public.example_table;"
```

预期结果是 `permission denied`。

确认 `dataman_sync` 可以写基础表时，使用事务回滚测试，避免留下测试数据：

```bash
docker exec -i dataman-postgres psql -U dataman_sync -d dataman_data -v ON_ERROR_STOP=1 -P pager=off <<'SQL'
BEGIN;
INSERT INTO public.example_table (id, name, status, imported_at)
VALUES (-1, 'permission_check', 'ok', CURRENT_TIMESTAMP);
SELECT count(*) FROM public.example_table WHERE id = -1;
ROLLBACK;
SQL
```

### 8. AskData 手动建场景时的绑定方式

手动创建 AskData Scene 时，绑定数据源和视图：

```text
data_source_name = dataman_data
view_name = reporting.vw_example_table
```

配置数据源时使用只读账号并启用只读设置。Scene 的语义说明由业务侧维护；数据库层只保证 AskData 能读取到稳定、只读、带注释的 `reporting` 视图。

## 8. 数据质量与验收

### 同步层

- 核对上游与目标的筛选口径、行数及业务指标；全量同步的差异需可解释。
- 检查主键空值/重复、重复重试、必填字段空值率、字段截断、编码错误和类型转换失败。
- 检查字典关联覆盖率、未知代码、代码与名称的唯一关系，以及金额、日期、时区和删除处理。
- 确认增量水位前进，失败能从上次成功位置安全恢复，重复运行不会重复或漏数。

### 查询层与 AskData

- 确认 `reporting` 视图的行粒度正确，关联不会造成意外倍增；在同一过滤口径下抽样对比上游关键计数和金额。
- 用 `dataman_reader` 做权限正反向验证：允许读目标视图，不允许读无授权同步表或写入对象。
- 为 Scene 准备基准问题，覆盖明细、筛选、分组统计、金额/比率、多条件、空值、未知代码和超出场景范围的问题；将结果与可复现 SQL 对比。
- schema 或语义变化后重新检查数据源、Scene Snapshot 和激活状态，不能只确认页面有返回结果。

## 9. 变更发布、回滚与运维

结构、字段或口径变化时，逐项检查上游视图/抽取配置、同步平台映射和调度、PostgreSQL 运行库、初始化脚本、字段/语义文档、`reporting` 视图与权限、AskData Snapshot、UAT 和告警说明。

- 发布前保留 DDL、视图定义和同步配置的可恢复版本；优先新增兼容字段或视图，再迁移消费者。
- 同步异常时先暂停或回退同步任务；未确认影响范围前不要清空正式表，也不要临时开放同步表给 AskData。
- 必要时回到上一个有效 Scene Snapshot 或暂时停用场景。回滚后核对数据行数、水位、权限和 Scene 状态，并记录原因及恢复时间。
- 运维记录至少包含最后成功时间、水位/批次、读取和写入行数、增删改数量、耗时、失败信息及重试结果。

## 10. 新场景交付清单

### 设计

- [ ] 明确业务负责人、使用范围、敏感级别、数据粒度和稳定主键。
- [ ] 确认同步模式、水位、刷新频率、删除/重放策略及允许延迟。
- [ ] 确认字段映射、字典/联动规则、单位、时区、空值规则和质量标准。

### 实施

- [ ] 配置最小权限的上游只读与 PostgreSQL 同步凭据；配置幂等、重试、日志和告警。
- [ ] 创建并注释同步表，创建白名单 `reporting` 视图，验证粒度和权限。
- [ ] 同步更新运行库迁移、Docker 初始化脚本、字段映射文档和 Scene 语义。

### 上线

- [ ] 完成首次全量/回填，核对行数、主键、字典和关键业务指标。
- [ ] 验证增量重复运行幂等、失败恢复和删除规则；完成只读权限正反向测试。
- [ ] Scene Snapshot 构建成功，UAT 通过，监控、负责人和回滚方案已交接。

## 11. 当前已建示例

虚拟机清单已按上述方式接入：

```text
基础同步表: public.vm_list
AskData视图: reporting.vw_vm_list
同步账号: dataman_sync 写 public.vm_list
查询账号: dataman_reader 读 reporting.vw_vm_list
```

手动建虚拟机清单 Scene 时使用：

```text
data_source_name = dataman_data
view_name = reporting.vw_vm_list
```

## 12. 提交流程建议

完成数据库对象后，至少执行：

```bash
git diff --check -- docker/dataman-postgres/init/001-create-information-project-schema.sh configs/DATAMAN_DATA_SYNC_GUIDE.md
```

如果只改数据库初始化脚本和文档，不需要重启 DataMan 服务；如果改了 AskData 代码或前端页面，再按项目验证清单执行对应检查。

## 13. 相关文档

- 信息化项目端到端数据流：`configs/INFORMATION_PROJECT_DATA_FLOW.md`
- AskData 数据源配置示例：`configs/ask-data.example.toml`
- PostgreSQL 初始化定义：`docker/dataman-postgres/init/001-create-information-project-schema.sh`
