# 基于 Neo4j 的三层全局本体建设与召回技术方案

## 1. 方案目标

本方案把 Datrix 的全局本体改造成一个可版本化、可审核、可追溯、可按图扩展的 Neo4j 属性图，用于**查数完成后的数据分析**。

本体不参与场景选择、字段选择、SQL 生成或跨场景查询。场景 Agent 仍按场景配置和字段信息查数；查询结果返回后，系统根据结果元数据从活动版全局本体召回分析所需的业务语义和约束，交给分析 Agent 生成最终解释。

Neo4j 采用属性图模型：节点和关系都可以携带属性，适合表达实体、指标、场景绑定和跨场景分析契约。当前关系型元数据库继续保存 Markdown、草稿、审核记录和版本信息；Neo4j 保存已发布本体快照的运行时图。

## 2. 当前实现基线

当前项目已经具备以下基础：

- 本体 Markdown 草稿、编译结果和活动快照生命周期；
- `OntologyGraph` 的节点和边结构；
- 场景实体、指标、场景绑定、分析维度、分析规则和跨场景路径；
- 查数后的本体召回入口 `OntologyAnalysisService`；
- 结果列、语义键、场景、时间和粒度元数据；
- 指标只能从语义文档中产生，数据字典只补充字段映射和属性；
- 现有召回主要在内存中按节点分数筛选，尚未进行通用的图邻居扩展；
- 本体当前存储在关系型元数据库中，`graph_json` 和 `prompt_projection_json` 以 JSON 保存，不是图数据库。

因此，本方案优先复用现有生成、审核和快照流程，把“发布后的运行时图存储”和“图查询召回”替换为 Neo4j，而不是重写整个本体生成链路。

## 3. 总体架构

```text
场景语义文档 + 数据字典
          │
          ▼
场景级抽取 / 全局本体汇总
          │
          ▼
关系库中的 Markdown 草稿
          │
          ▼
编译、审核、版本校验
          │
          ├── 生成 OntologyGraph / JSON（兼容现有接口）
          └── 发布到 Neo4j 的新 Ontology Snapshot
                         │
                         ▼
                   激活快照指针
                         │
查询完成 ──► 分析请求包 ──► Neo4j 图召回 ──► 分析 Agent ──► 最终回复
```

组件职责：

| 组件 | 职责 |
| --- | --- |
| 关系型元数据库 | 原始 Markdown、草稿、审核记录、布局、来源快照、发布状态和回滚依据 |
| 本体编译器 | Markdown/JSON 转为规范化的三层对象和关系 |
| Neo4j | 已发布快照的节点、关系、版本和运行时图查询 |
| 本体同步器 | 将一个已校验版本原子写入 Neo4j，并校验数量和哈希 |
| 图召回服务 | 从结果锚点开始受控扩展，返回分析上下文和召回证据 |
| 分析 Agent | 结合查询结果和已召回本体生成业务分析，不生成新的本体映射 |

关系型数据库仍是本体编辑和审核的事实来源；Neo4j 是活动本体的运行时投影。不能只修改 Neo4j 而绕过草稿、审核和版本流程。

## 4. 三层图模型

### 4.1 业务语义层

业务语义层描述“业务对象和指标是什么”。

节点建议使用以下标签：

```text
(:OntologySnapshot)
(:Domain)
(:Entity)
(:Metric)
(:Dimension)
(:Unit)
(:TimeBasis)
```

主要属性：

```text
id                 稳定全局 ID，例如 project.budget_execution_amount
name               展示名称
aliases            别名数组
domain_id          领域命名空间
definition         业务定义
unit               单位
currency           币种
aggregation        聚合方式
grain              统计粒度
time_basis         时间依据
formula            声明式公式，只引用已声明指标
status             candidate / confirmed / deprecated / conflicted
```

主要关系：

```text
(:OntologySnapshot)-[:CONTAINS]->(:Entity|:Metric|:Dimension)
(:Metric)-[:BELONGS_TO]->(:Entity)
(:Entity)-[:RELATED_TO]->(:Entity)
(:Metric)-[:RELATED_TO_METRIC]->(:Metric)
(:Metric)-[:USES_DIMENSION]->(:Dimension)
(:Metric)-[:USES_UNIT]->(:Unit)
(:Metric)-[:USES_TIME_BASIS]->(:TimeBasis)
```

不同领域的同名对象默认保持不同 ID。例如 `project.project` 和 `research.research_project` 不能因为名称相似自动合并。

### 4.2 分析解释层

分析解释层描述“查到结果后如何解释”。

节点建议使用：

```text
(:AnalysisRule)
(:AnalysisDimension)
(:AnalysisPath)
(:ClarificationRule)
```

主要属性：

```text
trigger              触发条件的结构化表达
guidance             给分析 Agent 的解释指导
interpretation       指标解释
common_anomalies     常见异常
positive_direction   趋势方向
allowed_analyses     允许的分析
forbidden_inferences 禁止的推断
```

主要关系：

```text
(:AnalysisRule)-[:APPLIES_TO_METRIC]->(:Metric)
(:AnalysisRule)-[:APPLIES_TO_ENTITY]->(:Entity)
(:AnalysisDimension)-[:APPLIES_TO_METRIC]->(:Metric)
(:AnalysisPath)-[:FROM_METRIC]->(:Metric)
(:AnalysisPath)-[:TO_METRIC]->(:Metric)
(:AnalysisPath)-[:REQUIRES_ALIGNMENT]->(:AlignmentCondition)
```

分析规则不依赖模糊文本相似度触发，而是由已绑定的指标、实体、时间和粒度条件触发。

### 4.3 映射治理层

映射治理层描述“本体概念对应哪些场景数据，以及这条关系是否可信”。

节点建议使用：

```text
(:Scene)
(:SceneSnapshot)
(:ResultBinding)
(:Evidence)
(:ReviewRecord)
(:MappingSystem)
```

`ResultBinding` 是结果召回的主要锚点，建议包含：

```text
scene_id
scene_snapshot_id
semantic_key
result_key_pattern
label_aliases
column_role
aggregation
unit
grain
time_basis
status
evidence_ids
```

主要关系：

```text
(:Scene)-[:HAS_SNAPSHOT]->(:SceneSnapshot)
(:SceneSnapshot)-[:DECLARES_BINDING]->(:ResultBinding)
(:ResultBinding)-[:MAPS_TO]->(:Metric|:Dimension|:Entity)
(:ResultBinding)-[:SUPPORTED_BY]->(:Evidence)
(:Evidence)-[:FROM_DOCUMENT]->(:SceneSnapshot)
(:ReviewRecord)-[:REVIEWS]->(:ResultBinding|:AnalysisPath|:Metric)
```

物理字段名可以作为映射属性保存，但不能成为全局指标 ID，也不能通过字段名自动创建指标。

## 5. 快照和版本模型

Neo4j 中所有运行时节点和关系都必须带 `snapshot_id`，或者通过 `IN_SNAPSHOT` 连接到一个 `OntologySnapshot` 节点。推荐采用快照节点加关系过滤的方式：

```text
(:OntologySnapshot {
  snapshot_id,
  ontology_id,
  revision,
  content_hash,
  status,
  created_at,
  activated_at,
  compiler_version
})
```

每个业务节点和关系包含：

```text
snapshot_id
object_id / relation_id
source_scene_ids
status
provenance
```

发布流程：

1. 从关系库读取已通过校验的本体修订版；
2. 编译成规范化三层对象；
3. 在 Neo4j 中创建新的 `OntologySnapshot(status='building')`；
4. 在同一事务或可重试的批处理流程中写入节点、关系、绑定和证据；
5. 校验节点数、关系数、必要绑定、引用完整性和内容哈希；
6. 将快照标记为 `ready`；
7. 关系库和 Neo4j 的活动指针按顺序切换；
8. 旧快照保留一段时间，用于回滚和历史分析；
9. 切换失败时不得把半成品快照提供给召回服务。

运行时每次请求显式携带 `snapshot_id`，不能依赖 Neo4j 中没有版本条件的全库查询。分析结果同时记录 `ontology_snapshot_id`，保证结论可以复现。

## 6. 图召回原理

召回不是让 LLM 直接“猜本体”，也不是把整张图交给 LLM。采用“结构化锚定 + 图扩展 + 条件过滤”的方式。

### 6.1 分析请求包

查数完成后，服务构造：

```json
{
  "question": "按项目比较预算执行和财务支付进度",
  "ontology_snapshot_id": "onto_x",
  "scene_ids": ["project_analysis", "finance_payment"],
  "authorized_scene_ids": ["project_analysis", "finance_payment"],
  "result_columns": [],
  "result_profiles": [],
  "filters": [],
  "result_status": {}
}
```

结果列至少包含：`scene_id`、`key`、`label`、`semantic_key`、`type`、`unit`、`aggregation`、`grain` 和 `time_basis`。结果画像包含时间范围、时间粒度、业务粒度、是否截断和空值状态。

### 6.2 锚点解析

匹配顺序固定为：

1. `scene_id + semantic_key` 精确匹配 `ResultBinding`；
2. `scene_id + result_key` 精确匹配；
3. 已审核的结果列模式和别名匹配；
4. 对仍未绑定的列使用关键词或向量检索找候选；
5. 模糊候选保持 `candidate/unmatched`，不能直接变成确认映射。

映射治理层决定“结果列是什么”。向量检索最多用于发现候选，不能覆盖已审核映射，也不能单独触发跨场景分析。

### 6.3 图向外扩展

从已确认的指标或维度锚点开始，按白名单关系扩展：

```text
ResultBinding
  → MAPS_TO → Metric / Dimension
  → BELONGS_TO → Entity
  → USES_DIMENSION / RELATED_TO_METRIC → 相关语义
  → APPLIES_TO_METRIC → AnalysisRule
  → FROM_METRIC / TO_METRIC → AnalysisPath
```

扩展规则：

- 业务语义层最多扩展 1～2 跳；
- 分析规则只扩展到当前结果实际命中的指标或实体；
- 映射治理层始终保留来源场景、快照和证据；
- `AnalysisPath` 只有 `status=confirmed` 且两侧结果均存在时才扩展；
- 时间依据、时间范围、时间粒度和业务粒度不满足桥接条件时，返回禁止比较的说明，不返回可执行分析路径；
- 所有节点和关系都必须通过权限和 `snapshot_id` 过滤；
- 设置最大节点数、关系数和查询超时，避免整图扩散。

### 6.4 分析上下文

图召回服务返回结构化上下文：

```text
anchors
business_concepts
analysis_rules
analysis_dimensions
cross_scene_paths
matched_bindings
unmatched_columns
evidence
ontology_snapshot_id
retrieval_trace
```

LLM 只读取查询结果和这份上下文。它可以组织解释、描述趋势和指出异常，但不能凭空增加指标、映射或跨场景关系。

## 7. Neo4j 访问设计

后端使用官方 Neo4j Python Driver，通过连接池访问数据库；图查询使用固定的参数化 Cypher 模板，禁止让 LLM 生成 Cypher。写入发布使用显式事务和批量 `UNWIND`，读查询使用只读事务。

建议配置：

```text
DATAMAN_NEO4J_URI=bolt://neo4j:7687
DATAMAN_NEO4J_USERNAME=neo4j
DATAMAN_NEO4J_PASSWORD=<secret-managed-value>
DATAMAN_NEO4J_DATABASE=neo4j
DATAMAN_NEO4J_ENABLED=true
DATAMAN_NEO4J_CONNECT_TIMEOUT_SECONDS=5
DATAMAN_NEO4J_MAX_CONNECTION_POOL_SIZE=20
```

密码不能写入仓库或普通配置文件，应使用现有部署环境的密钥注入机制。未启用 Neo4j 时，系统可以暂时回退到关系库快照召回；生产环境切换完成后，应通过健康检查明确显示当前后端。

建议的唯一性约束和索引：

```cypher
CREATE CONSTRAINT ontology_snapshot_id IF NOT EXISTS
FOR (n:OntologySnapshot) REQUIRE n.snapshot_id IS UNIQUE;

CREATE CONSTRAINT ontology_object_key IF NOT EXISTS
FOR (n:OntologyObject) REQUIRE (n.snapshot_id, n.object_id) IS UNIQUE;

CREATE INDEX ontology_binding_key IF NOT EXISTS
FOR (n:ResultBinding) ON (n.snapshot_id, n.scene_id, n.semantic_key);

CREATE INDEX ontology_metric_domain IF NOT EXISTS
FOR (n:Metric) ON (n.snapshot_id, n.domain_id);
```

具体约束语法需按部署的 Neo4j 版本在上线前验证；迁移脚本必须可重复执行。

## 8. 本体管理页改造

现有本体页保留 Markdown、草稿、编译和发布能力，增加 Neo4j 运行状态和三层视图：

- 业务语义层：领域、实体、指标、关系和指标口径；
- 分析解释层：指标解释、分析维度、分析规则和跨场景契约；
- 映射治理层：场景绑定、结果列模式、证据、审核状态和来源版本；
- 图谱视图：按快照和领域过滤，支持从指标沿关系查看邻居；
- 发布状态：关系库修订版状态、Neo4j 同步状态、节点/关系计数和内容哈希；
- 失败重试和回滚：显示失败原因，不允许激活未完成的图快照。

结果页增加“本体依据”：展示本次命中的绑定、扩展到的指标和规则、跨场景条件、未绑定列、证据和 `ontology_snapshot_id`。

## 9. API 和代码拆分

建议新增以下后端模块：

```text
ontology/neo4j_client.py       连接、健康检查、事务封装
ontology/neo4j_schema.py       约束、索引和迁移
ontology/neo4j_writer.py       快照批量写入、校验和删除
ontology/neo4j_reader.py       锚点查询、邻居扩展、桥接检查
ontology/snapshot_sync.py      关系库修订版到 Neo4j 的同步编排
ontology/graph_retrieval.py    查数结果驱动的召回服务
```

建议接口：

```text
GET  /api/v1/ask-data/ontology/graph/health
POST /api/v1/ask-data/ontology/snapshots/{revision}/sync
GET  /api/v1/ask-data/ontology/snapshots/{snapshot_id}/sync-status
POST /api/v1/ask-data/ontology/retrieval-preview
GET  /api/v1/ask-data/ontology/objects/{object_id}/neighbors
```

现有生成、校验、发布 API 继续保留；`build_snapshot` 成功后增加同步步骤，激活 API 只有在 Neo4j 快照为 `ready` 时才能切换。

## 10. 分阶段开发计划

### 阶段一：基础设施和只读验证

- 增加 Neo4j 本地开发和测试配置；
- 实现 Driver、健康检查、连接池和配置校验；
- 定义约束、索引和同步状态；
- 将现有已发布 `OntologyGraph` 写入 Neo4j；
- 实现图数量、引用和哈希校验；
- 保留关系库回退；
- 用查询脚本验证实体、指标、绑定和关系查询。

### 阶段二：快照同步和发布集成

- 将三层编译对象写入 Neo4j；
- 实现 building/ready/active/superseded 状态；
- 发布时原子写入新快照并切换活动指针；
- 增加失败重试、回滚和幂等；
- 在查询运行记录中保存 Neo4j 的 `ontology_snapshot_id`。

### 阶段三：图扩展召回

- 将结果列和结果画像标准化为分析请求包；
- 实现精确绑定锚点查询；
- 实现业务语义层和分析解释层的受控多跳扩展；
- 实现跨场景桥接的条件校验；
- 保留未绑定列和召回轨迹；
- 与现有 JSON 召回并行对照，确认结果一致后切换默认路径。

### 阶段四：前端三层展现和治理

- 本体页增加三层标签页和图谱视图；
- 增加映射审核、证据查看和桥接审核；
- 增加 Neo4j 同步状态、失败重试和回滚操作；
- 结果页展示本体依据和召回轨迹；
- 增加按领域、场景、版本和状态过滤。

### 阶段五：性能与向量检索增强

- 统计锚点命中率、未绑定率、平均扩展节点数和查询耗时；
- 仅在精确绑定不足时增加语义向量候选召回；
- 向量候选必须回到映射状态和图关系校验；
- 根据真实规模调整索引、批量写入和缓存策略。

## 11. 验收标准

### 数据和版本

- 每个 Neo4j 节点和关系都能追溯到 `snapshot_id`；
- 新快照写入失败不会影响当前活动快照；
- 激活后可按 `snapshot_id` 完整复现一次召回；
- 关系库修订版、Neo4j 快照和来源场景版本一致。

### 三层模型

- 指标、实体和关系属于业务语义层；
- 分析规则、维度和桥接契约属于分析解释层；
- 场景绑定、证据、审核和来源版本属于映射治理层；
- 指标不能由数据字典字段名自动生成。

### 召回

- 精确语义键命中时不依赖 LLM 或向量；
- 图扩展只沿允许关系类型和规定跳数进行；
- 未审核桥接不会进入可执行分析上下文；
- 时间或粒度不匹配时明确返回禁止比较原因；
- 未绑定结果列不会被自动猜测成指标；
- 结果中记录命中对象、扩展关系、证据和快照版本。

### 运维

- Neo4j 不可用时有明确健康状态和可控回退策略；
- 密钥不进入仓库、日志或前端；
- 写入和读取都有超时、重试和指标；
- 关系库和 Neo4j 的同步可以幂等执行。

## 12. 主要风险和决策

| 风险 | 处理方式 |
| --- | --- |
| Neo4j 与关系库出现版本不一致 | 以关系库审核版本为源，发布前做哈希和计数校验，激活前检查同步状态 |
| 图扩展过宽导致上下文膨胀 | 白名单边类型、最大跳数、节点上限和结果缓存 |
| 模糊匹配导致错误指标 | 精确绑定优先，向量只产生候选，候选不得自动确认 |
| 跨场景关系被误当作 JOIN | `AnalysisPath` 仅表达分析契约，必须通过结果对齐检查 |
| Neo4j 暂时不可用 | 保留已发布 JSON 快照回退，生产监控告警并限制新快照激活 |
| 过早引入向量检索增加复杂度 | 先完成结构化映射和图扩展，用指标验证后再增加向量候选层 |

## 13. 官方资料

- [Neo4j 图数据库概念](https://neo4j.com/docs/getting-started/appendix/graphdb-concepts/)
- [Neo4j Python Driver Manual](https://neo4j.com/docs/python-manual/current/)
