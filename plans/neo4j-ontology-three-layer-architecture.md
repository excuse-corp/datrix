# 按三层建设本体并存储到 Neo4j 的技术方案

## 1. 方案范围

本方案只解决：

1. 按“业务语义层、分析解释层、映射治理层”建设全局本体；
2. 将审核通过的全局本体版本可靠地存储到 Neo4j。

本方案暂不设计查数后的本体召回、图遍历召回、向量检索和分析 Agent 提示词。召回会以本方案生成的 Neo4j 图为数据基础，另行制定方案。场景仍然通过字段信息和场景配置生成 SQL，本体建设不参与 SQL 生成。

## 2. 设计原则

- 指标只能来自语义文档中明确声明或描述的指标，不能由数据字典字段名启发式生成。
- 先形成场景级本体抽取结果，再汇总为全局本体，保留来源场景和证据。
- 业务定义、分析规则、数据映射分别建模，字段映射不能成为指标定义。
- 关系型元数据库保存原始文档、草稿、审核记录和版本；Neo4j 保存已发布本体图。
- 每次发布形成独立不可变图快照；新版本校验通过后才能激活，失败可以回滚。
- 候选对象可进入草稿，只有审核通过的对象和关系才进入可用快照。
- 本阶段不把项目、金额、支付记录等业务事实批量写入 Neo4j，只建设本体和治理信息。

## 3. 当前实现与目标差距

当前已有场景语义 Markdown、数据字典、场景快照、LLM/规则生成、`OntologyGraph`、Markdown 草稿编译发布和关系库 JSON 快照。首轮实施已增加发布时三层规范化、LLM 指标来源追溯、Neo4j 快照投影、图状态 API 和管理页状态展示。仍待补齐的是更完整的三层编辑/审核表单、历史规则草稿追溯治理、数据库同步状态落库，以及召回读图（召回另有 handoff，当前方案范围不做）。

目标是在现有生成和审核链路上增加三层规范化模型与 Neo4j 发布投影，不改变场景查询链路。

## 4. 总体架构

```text
场景语义文档 + 数据字典
          ↓
场景级本体抽取 → 全局本体汇总 → 三层规范化编译
                                      ↓
                              关系库本体草稿
                                      ↓ 人工审核
                              可发布关系库修订版
                                      ↓
                            Neo4j 新建 OntologySnapshot
                                      ↓ 校验
                                ready → active
```

关系型元数据库是本体编辑和审核的管理源；Neo4j 是审核通过版本的图存储。`graph_json` 在迁移期间保留为兼容投影和故障回退。当前发布通过 Neo4j 时，三层模型是已编译图上的逻辑投影，并非三套独立的编辑模型。

## 5. 三层本体模型

### 5.1 业务语义层

回答“业务上有什么对象和概念”。

节点：`Domain`、`Entity`、`Metric`、`Dimension`、`Unit`、`TimeBasis`。

指标至少包含：

```text
object_id, name, aliases, domain_id, entity_id, definition,
analysis_meaning, unit, currency, aggregation, grain, time_basis,
formula, status
```

指标定义、公式、单位和口径来自语义文档；数据字典只为已声明指标补充字段层属性。

关系：

```text
Metric  -[:BELONGS_TO]->        Entity
Entity  -[:RELATED_TO]->        Entity
Metric  -[:RELATED_TO_METRIC]-> Metric
Metric  -[:USES_DIMENSION]->    Dimension
Metric  -[:USES_UNIT]->         Unit
Metric  -[:USES_TIME_BASIS]->   TimeBasis
```

不同领域的同名对象默认不合并，例如 `project.project` 与 `research.research_project` 必须保持不同命名空间。

### 5.2 分析解释层

回答“结果如何解释，哪些推断受限”。

节点：`AnalysisRule`、`AnalysisDimension`、`AnalysisPath`、`ClarificationRule`、`AlignmentCondition`。

规则属性：

```text
trigger, guidance, interpretation, common_anomalies,
positive_direction, allowed_analyses, forbidden_inferences, status
```

关系：

```text
AnalysisRule      -[:APPLIES_TO_METRIC]-> Metric
AnalysisRule      -[:APPLIES_TO_ENTITY]-> Entity
AnalysisDimension -[:APPLIES_TO_METRIC]-> Metric
AnalysisPath      -[:FROM_METRIC]->       Metric
AnalysisPath      -[:TO_METRIC]->         Metric
AnalysisPath      -[:REQUIRES]->          AlignmentCondition
```

跨场景路径是分析契约，不是 SQL JOIN，必须记录两端指标、业务映射、时间/粒度条件、允许分析、禁止推断、证据和审核状态。

### 5.3 映射治理层

回答“概念对应哪些场景数据，以及关系是否可信”。

节点：`Scene`、`SceneSnapshot`、`ResultBinding`、`Evidence`、`ReviewRecord`、`SourceDocument`。

`ResultBinding` 至少包含：

```text
binding_id, scene_id, scene_snapshot_id, semantic_key,
result_key_patterns, label_aliases, column_role, field_name,
aggregation, unit, grain, time_basis, status
```

关系：

```text
Scene         -[:HAS_SNAPSHOT]->     SceneSnapshot
SceneSnapshot -[:DECLARES_BINDING]-> ResultBinding
ResultBinding -[:MAPS_TO]->          Metric|Dimension|Entity
ResultBinding -[:SUPPORTED_BY]->     Evidence
Evidence      -[:FROM_DOCUMENT]->    SourceDocument
ReviewRecord  -[:REVIEWS]->          ResultBinding|Metric|AnalysisPath
```

物理字段名不能成为全局指标 ID，也不能通过“金额”“数量”等字段关键词生成指标。

## 6. 场景级到全局本体

### 场景级抽取

每个场景先生成：场景/领域标识、实体及证据、语义文档明确声明的指标及证据、场景内关系、分析能力、澄清规则、绑定候选和来源版本。

LLM 模式可以提取业务语义正文中明确描述的指标；规则模式只读取结构化 `metrics`。数据字典只补充已声明指标的字段、单位和语义键。

### 全局汇总

全局汇总读取场景级抽取结果，完成领域内同义归并、领域命名空间处理、实体/指标关系整理、分析规则和跨场景契约生成，并保留来源场景、证据、绑定和冲突。

全局汇总不得凭空新增指标。每个全局指标必须有 `source_metric_refs`，至少指向场景 ID、场景级指标 ID、语义文档区段和证据；没有来源追溯的指标只能作为候选，不能发布。

## 7. 三层规范化编译

Markdown 适合人工编辑，发布前编译为：

```json
{
  "business_semantics": {"domains": [], "entities": [], "metrics": [], "dimensions": [], "relations": []},
  "analysis_explanations": {"rules": [], "dimensions": [], "paths": [], "clarifications": []},
  "mapping_governance": {"scenes": [], "scene_snapshots": [], "bindings": [], "evidence": [], "reviews": []}
}
```

编译校验至少包括：对象 ID 和领域命名空间唯一；指标有语义文档证据和来源场景；公式只引用已声明指标；关系两端存在且类型正确；绑定目标和来源快照存在；分析规则引用存在的对象；跨场景路径有两个场景且契约完整；图属性不包含 SQL、连接信息、密码或 API key；候选、确认、拒绝和废弃状态符合发布规则。

## 8. Neo4j 图模型

### 快照节点

```text
(:OntologySnapshot {
  snapshot_id, ontology_id, revision, status,
  content_hash, compiler_version, source_scene_count,
  node_count, relationship_count, created_at, activated_at
})
```

所有对象和关系带 `snapshot_id`，并通过 `(:OntologySnapshot)-[:CONTAINS]->(...)` 归属到快照。任何维护和查询都必须带快照条件，不能混用版本。

### 标签

```text
OntologySnapshot, Domain, Entity, Metric, Dimension, Unit, TimeBasis,
AnalysisRule, AnalysisDimension, AnalysisPath, ClarificationRule,
AlignmentCondition, Scene, SceneSnapshot, ResultBinding, Evidence,
ReviewRecord, SourceDocument
```

每个对象至少有 `snapshot_id`、`object_id`、`name`、`status`、`source_scene_ids` 和 `provenance`。

### 关系白名单

```text
CONTAINS, BELONGS_TO, RELATED_TO, RELATED_TO_METRIC,
USES_DIMENSION, USES_UNIT, USES_TIME_BASIS, APPLIES_TO_METRIC,
APPLIES_TO_ENTITY, FROM_METRIC, TO_METRIC, REQUIRES, HAS_SNAPSHOT,
DECLARES_BINDING, MAPS_TO, SUPPORTED_BY, FROM_DOCUMENT, REVIEWS
```

关系类型固定，不允许由 LLM 或 Markdown 任意创建。关系属性至少包含 `snapshot_id`、`relation_id`、`status`、`source_scene_ids` 和 `provenance`。

### 约束和索引

以 Neo4j 5.26 为准编写可重复迁移；具体语法上线前在实际实例验证：

```cypher
CREATE CONSTRAINT ontology_snapshot_id IF NOT EXISTS
FOR (n:OntologySnapshot) REQUIRE n.snapshot_id IS UNIQUE;

CREATE INDEX ontology_metric_id IF NOT EXISTS
FOR (n:Metric) ON (n.snapshot_id, n.object_id);

CREATE INDEX ontology_binding_key IF NOT EXISTS
FOR (n:ResultBinding) ON (n.snapshot_id, n.scene_id, n.semantic_key);

CREATE INDEX ontology_scene_snapshot IF NOT EXISTS
FOR (n:SceneSnapshot) ON (n.snapshot_id, n.scene_id, n.scene_snapshot_id);
```

如果部署版本不支持跨标签复合唯一约束，则为每个业务标签创建等价约束。

## 9. 发布、同步和回滚

### 发布前

1. 从关系库读取本体草稿和来源场景快照；
2. 编译为三层规范化模型；
3. 执行结构、证据、来源版本和审核状态校验；
4. 计算规范化模型内容哈希；
5. Neo4j 不可用时发布失败，不改变当前活动版本。

### 写入 Neo4j

1. 创建 `OntologySnapshot(status='building')`；
2. 使用参数化 Cypher 和 `UNWIND` 批量写入三层节点；
3. 批量写入关系、场景快照、绑定、证据和审核记录；
4. 校验节点数、关系数、引用、快照归属和内容哈希；
5. 通过后更新为 `ready`；失败则标记 `failed`，不影响旧版本。

### 激活和回滚

- 只有 Neo4j `ready` 快照才能激活；
- 激活时切换明确的活动快照指针；
- 旧版本保留并标记 `superseded`；
- 失败时保持旧活动版本；
- 回滚只切换到已校验旧快照，不重新生成内容。

同步使用 `ontology_id + revision + content_hash` 作为幂等键。重复同步不产生重复节点和关系；部分失败重试时清理同一 `snapshot_id` 的 building/failed 数据或回滚事务后重写。

## 10. 代码与接口改造

首轮实现已经落地为：

```text
ontology/graph_model.py        三层 manifest、对象/关系层级和引用校验
ontology/neo4j_schema.py       约束和索引
ontology/neo4j_store.py        Driver、连接池、健康检查、批量写入和快照状态
ontology/service.py             发布同步、激活和失败回滚编排
```

现有模块调整：

| 模块 | 调整 |
| --- | --- |
| `llm_generator.py` | 输出场景级/全局候选和来源指标引用 |
| `generator.py` | 规则模式只读取语义文档显式指标 |
| `llm_validator.py` | 增加全局对象到场景级对象的追溯校验 |
| `markdown.py` | 将三层章节编译为规范化对象，兼容 `OntologyGraph` |
| `service.py` | 发布时调用同步器并记录同步状态 |
| `repository.py` | 继续保存关系库本体版本和 JSON 兼容投影；Neo4j 计数/错误目前通过同步状态 API 实时读取，后续可增加关系库同步状态表 |

本阶段实际接口只处理建设和同步：

```text
GET  /api/v1/ask-data/ontology/neo4j/health
GET  /api/v1/ask-data/ontology/snapshots/{snapshot_id}/sync-status
POST /api/v1/ask-data/ontology/snapshots/{snapshot_id}/sync
```

快照构建和激活仍使用原有的 snapshot API；Neo4j 同步在
`OntologyLifecycleService.build_snapshot()` 中自动执行，激活时由
`OntologyLifecycleService.activate()` 进行 Neo4j/关系库状态协调。当前没有单独的
revision 同步接口，也没有把 Neo4j 召回接口放在本阶段。

不在本阶段增加召回接口。

## 11. 前端三层展现

本体管理页以全局本体快照为中心，增加三个视图：

- **业务语义层**：领域、实体、指标、维度、关系、指标口径、来源场景和对象状态；
- **分析解释层**：指标解释、异常说明、分析维度、跨场景契约、允许分析和禁止推断；
- **映射治理层**：场景、场景快照、语义键、绑定目标、字段映射、证据和审核记录。

页面显示关系库修订版、Neo4j 同步状态、snapshot ID、节点/关系数、内容哈希、失败原因、重试和回滚入口。前端本阶段不做运行时召回预览。

## 12. 配置与部署

Neo4j 使用 [Neo4j 接入信息](../docs/neo4j-access.md) 中的 Docker Compose 部署，本地 Bolt 地址为 `bolt://127.0.0.1:7670`，数据库名为 `neo4j`。

建议配置：

```text
DATAMAN_NEO4J_ENABLED=true
DATAMAN_NEO4J_URI=bolt://127.0.0.1:7670
DATAMAN_NEO4J_USERNAME=neo4j
DATAMAN_NEO4J_PASSWORD=<secret-managed-value>
DATAMAN_NEO4J_DATABASE=neo4j
DATAMAN_NEO4J_CONNECT_TIMEOUT_SECONDS=5
DATAMAN_NEO4J_MAX_CONNECTION_POOL_SIZE=20
```

密码只能通过 `.env`、部署密钥或环境变量注入，不能写入仓库、Markdown、日志或前端代码。

## 13. 分阶段开发计划与首轮实施情况

| 阶段 | 状态 | 首轮实施情况 |
| --- | --- | --- |
| 三层规范化模型 | 已完成首轮 | 发布时为节点/关系加 layer；显式 `scene_binding` 生成 `ResultBinding`；场景快照生成 `SceneSnapshot`；LLM 全局指标要求追溯场景级指标 ID、语义区段和证据。独立编辑表单和更多来源对象仍待建设。 |
| Neo4j 基础设施 | 已完成首轮 | 接入 Python Driver、环境配置、健康检查、Neo4j 5.26 约束/索引和真实实例读写验证。 |
| 快照同步 | 已完成首轮 | 事务内重写单个 snapshot、固定类型标签和关系白名单、对象/关系计数校验、重复同步保持 active 状态。失败信息目前保存在 Neo4j 快照节点并由状态 API 返回，尚未写回关系库同步状态表。 |
| 发布流程集成 | 已完成首轮 | 构建时同步；启用 Neo4j 时同步失败会阻止关系库快照激活；Neo4j 未启用时保持旧 JSON 路径。 |
| 前端治理页 | 已完成首轮 | 显示三层对象数量、Neo4j 状态、当前快照节点/关系数，支持重试同步；更完整的对象详情、字段映射审核和回滚界面待做。 |

### 阶段一：三层规范化模型

- 定义三层 Pydantic 模型、ID 和状态；
- 增加全局指标到场景级指标的 `source_metric_refs`；
- 补充绑定、证据和审核记录；
- 调整 Markdown 章节和编译器；
- 增加来源、关系和跨层引用校验。

### 阶段二：Neo4j 基础设施

- 接入 Neo4j Python Driver、配置、健康检查、连接池和超时；
- 建立约束、索引和迁移脚本；
- 编写三层节点/关系写入器；
- 用固定样例完成读写集成测试。

### 阶段三：快照同步

- 实现关系库修订版到 Neo4j 的同步；
- 实现 building/ready/active/superseded/failed 状态；
- 增加批量写入、事务、幂等、重试和清理；
- 增加数量、引用和内容哈希校验；
- 将同步状态写回关系库。

### 阶段四：发布流程集成

- 将同步接入 `build_snapshot`/激活流程；
- 只有 Neo4j ready 快照才能激活；
- 保留 JSON 图兼容回退；
- 实现失败恢复和历史版本回滚；
- 记录来源场景快照版本。

### 阶段五：前端三层治理页面

- 增加三层标签页和对象详情；
- 增加映射、证据和跨场景契约审核；
- 增加 Neo4j 同步状态、重试和回滚；
- 增加领域、场景、状态和版本过滤。

首轮代码位置和后续召回工作边界见 [本体图谱召回开发交接文档](./ontology-graph-retrieval-handoff.md)。

召回、图邻居扩展和向量检索在本方案完成后另立方案。

## 14. 验收标准

### 本体建设

- 每个指标可追溯到语义文档、场景级指标和证据；
- 数据字典字段不能单独生成指标；
- 全局对象保留领域、来源场景、证据和版本；
- 三层对象可分别查看、审核和导出。

### Neo4j 存储

- 每个节点和关系属于一个 `snapshot_id`；
- 快照内对象和关系 ID 唯一；
- 所有引用指向存在对象；
- 重复同步不会产生重复数据；
- 新版本失败不影响当前 active 快照；
- 可从快照恢复节点数、关系数、来源版本和内容哈希。

### 发布运维

- Neo4j 不可用时发布明确失败，不切换活动版本；
- 可查看同步状态、错误、重试和回滚；
- 密码不出现在仓库、日志和前端；
- 关系库 JSON 图回退在迁移期间可用。

## 15. 主要代码位置

| 环节 | 代码/计划改造位置 |
| --- | --- |
| LLM 场景抽取与全局汇总 | `packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/llm_generator.py` |
| 规则生成 | `packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/generator.py` |
| 输出校验 | `packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/llm_validator.py` |
| Markdown 编译 | `packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/markdown.py` |
| 草稿、发布和快照 | `packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/service.py` |
| 元数据库仓库 | `packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/repository.py` |
| Neo4j 部署 | `docker/neo4j/compose.yml`、`docs/neo4j-access.md` |
| Neo4j 图模块 | `packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/graph_model.py`、`neo4j_schema.py`、`neo4j_store.py` |
