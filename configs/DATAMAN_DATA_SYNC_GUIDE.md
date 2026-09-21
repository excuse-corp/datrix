# DataMan 数据同步表与 AskData 查询视图新建指南

本文档说明以后在 `dataman_data` PostgreSQL 数据库中新建第三方同步表，以及为 AskData 暴露只读查询视图的标准步骤。

## 设计原则

- 第三方平台只写基础同步表，不直接写 `reporting` 查询视图。
- AskData 只绑定 `reporting` schema 下的视图，不直接绑定同步表。
- `dataman_sync` 用于第三方同步写入基础表。
- `dataman_reader` 用于 DataMan/AskData 查询，只授予 `reporting` 视图的 `SELECT` 权限。
- 新增对象需要同时更新运行中数据库和 Docker 初始化脚本，避免重建数据库后丢失结构。
- 源字段如果来自 Oracle/SQL Server 等异构库，需要转换为 PostgreSQL 支持的类型，例如 `VARCHAR2(n)` 改为 `varchar(n)`。

## 命名约定

- 基础同步表：优先使用第三方约定的表名，例如 `public.vm_list`。
- AskData 查询视图：使用 `reporting.vw_<table_name>`，例如 `reporting.vw_vm_list`。
- 主键约束：使用 `pk_<table_name>`，例如 `pk_vm_list`。
- 字段名：使用小写蛇形命名，避免空格、中文和大小写混用。

如果第三方同步表没有强制要求 `public` schema，也可以放在 `sync` schema；但无论基础表在哪个 schema，AskData 都只查询 `reporting` 视图。

## 操作步骤

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
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLE
  public.example_table TO dataman_sync;

GRANT USAGE ON SCHEMA reporting TO dataman_reader;
GRANT SELECT ON TABLE reporting.vw_example_table TO dataman_reader;
```

不要给 `dataman_reader` 授予基础同步表权限，除非有明确的临时排障需求。AskData 应通过 `reporting` 视图查询。

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

Scene 的语义说明由业务侧维护；数据库层只保证 AskData 能读取到稳定、只读、带注释的 `reporting` 视图。

## 当前已建示例

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

## 提交流程建议

完成数据库对象后，至少执行：

```bash
git diff --check -- docker/dataman-postgres/init/001-create-information-project-schema.sh configs/DATAMAN_DATA_SYNC_GUIDE.md
```

如果只改数据库初始化脚本和文档，不需要重启 DataMan 服务；如果改了 AskData 代码或前端页面，再按项目验证清单执行对应检查。
