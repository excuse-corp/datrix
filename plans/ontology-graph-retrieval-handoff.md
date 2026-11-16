# 本体图谱召回开发交接文档

本文交给下一阶段“查数完成后使用全局本体进行数据分析”的开发人员。当前阶段已经完成三层本体的发布编译和 Neo4j 存储，**没有把运行时召回切换到 Neo4j，也没有在本阶段实现图遍历召回**。

## 1. 已完成的边界

查数链路仍然由场景配置、查询能力和字段信息生成 SQL。本体在 SQL 执行完成后才使用。当前发布流程是：

```text
场景语义文档 + 数据字典
        ↓
场景级抽取 / 全局本体草稿
        ↓
Markdown 编译为 OntologyGraph
        ↓
三层规范化 manifest
        ↓
关系库保存草稿和快照
        ↓
Neo4j 写入同一个 ontology snapshot
        ↓
节点/关系校验 → ready → active
```

关系型元数据库仍是本体的管理源，Neo4j 是已发布版本的图投影。Neo4j 默认关闭，启用配置见 [`docs/neo4j-access.md`](../docs/neo4j-access.md)。

## 2. 三层和 Neo4j 数据模型

发布编译器将对象划分为三个固定的 `layer`：

| layer | 当前对象类型 | 作用 |
| --- | --- | --- |
| `business_semantics` | `entity`、`metric` 以及未归入其他层的业务对象 | 业务对象、指标、口径、单位、粒度、时间依据和业务关系 |
| `analysis_explanations` | `rule`、`analysis_dimension`、`analysis_path`、`analysis_rule`、`clarification_rule`、`alignment_condition` | 结果解释、异常说明、分析维度、跨场景分析契约和禁止推断 |
| `mapping_governance` | `scene`、`scene_snapshot`、`result_binding`、`evidence`、`review_record`、`source_document` | 场景版本、结果列语义绑定、来源和治理状态 |

当前 Neo4j 节点使用 `OntologyObject` 基础标签，并为已知 `object_type` 增加类型标签：`Entity`、`Metric`、`Scene`、`AnalysisRule`、`AnalysisDimension`、`AnalysisPath`、`SceneSnapshot`、`ResultBinding` 等。所有节点至少有：

```text
snapshot_id, ontology_id, revision, object_id, object_type, layer,
name, status, source_scene_ids, data_json, provenance_json
```

所有对象关系固定写入白名单类型，例如：

```text
BELONGS_TO, RELATED_TO, RELATED_TO_METRIC, PROVIDED_BY,
SCENE_BINDING, CROSS_SCENE_JOIN, CLARIFICATION_RULE,
HAS_SNAPSHOT, DECLARES_BINDING, MAPS_TO, SUPPORTED_BY,
FROM_DOCUMENT, REVIEWS, APPLIES_TO_METRIC, APPLIES_TO_ENTITY,
FROM_METRIC, TO_METRIC, REQUIRES
```

关系还带 `snapshot_id`、`relation_id`、`relation_type`、`layer`、`status`、`source_id`、`target_id` 和来源 JSON。所有 Cypher 必须带 `snapshot_id`，不能把不同版本的对象或关系混在一起。

Neo4j 的快照节点为：

```text
(:OntologySnapshot {
  snapshot_id, ontology_id, revision, status,
  content_hash, compiler_version, node_count,
  relationship_count, source_scene_count,
  source_scene_snapshots_json
})
```

对象通过 `(:OntologySnapshot)-[:CONTAINS]->(:OntologyObject)` 归属快照。`CONTAINS` 是存储归属关系，不能作为业务语义召回结果返回。

## 3. 绑定对象是后续召回的锚点

本体召回必须从查数结果提供的稳定元数据开始。优先级建议如下：

1. `scene_id + semantic_key` 精确命中 `ResultBinding`；
2. `scene_id + result_key` 或字段映射命中已审核绑定；
3. 对象名称和别名只用于候选匹配，不能单独确认指标；
4. 没有绑定的结果列保留为 `unmapped`，禁止根据“金额”“数量”等字段名猜测新指标。

显式 `scene_binding` 会在发布时保留原关系，并补充：

```text
Scene/SceneSnapshot -[:DECLARES_BINDING]-> ResultBinding
ResultBinding       -[:MAPS_TO]->       Metric|Dimension|Entity
```

`ResultBinding.data_json` 至少应包含 `scene_id`、`scene_snapshot_id`、`semantic_key`、结果列模式、字段名、聚合、单位、粒度、时间依据和审核状态。当前部分历史 Markdown 只含 `semantic_key`，召回实现要把缺失属性视为未知，不要推断。

## 4. 建议的召回接口

建议新增独立模块，不要把 Cypher 交给 LLM：

```text
ontology/neo4j_reader.py       只读快照和对象关系
ontology/graph_retrieval.py    精确绑定、白名单邻居扩展和裁剪
ontology/retrieval_models.py   召回结果和 trace 数据结构
```

建议接口形态：

```python
retrieve_analysis_context(
    *,
    ontology_snapshot_id: str,
    scene_id: str,
    result_columns: list[ResultColumnContext],
    user_question: str | None,
    authorized_scene_ids: set[str],
) -> OntologyRetrievalResult
```

返回值必须包括：

```text
ontology_snapshot_id
matched_bindings
matched_objects
analysis_rules
analysis_dimensions
cross_scene_contracts
unmapped_columns
retrieval_trace
```

`retrieval_trace` 至少记录每列的匹配方式、对象 ID、关系路径、过滤的原因、图快照和召回耗时。最终分析结果必须把 `ontology_snapshot_id` 和 trace 的摘要写入分析上下文，便于审计和复现。

## 5. 召回顺序和图扩展白名单

建议先做确定性匹配，再做有限图扩展：

```text
结果列元数据
  ↓
ResultBinding 精确匹配
  ↓
Metric/Dimension/Entity 对象
  ↓
白名单关系扩展
  ↓
分析规则、适用维度、已确认跨场景契约
  ↓
裁剪并形成分析上下文
```

第一版允许的扩展：

- 当前 `Metric` 的 `APPLIES_TO_METRIC`、`RELATED_TO_METRIC`、`USES_DIMENSION`；
- 当前 `Entity` 的 `BELONGS_TO`、`RELATED_TO`；
- 当前指标对应的 `AnalysisRule` 和 `AnalysisDimension`；
- 状态为 `confirmed` 且满足时间/粒度/场景权限条件的跨场景 `AnalysisPath` 或 `CROSS_SCENE_JOIN`。

第一版不允许：

- 沿任意关系无限扩展；
- 使用 `CONTAINS`、`PROVIDED_BY` 作为业务分析结论；
- 将 `candidate` 跨场景关系当作已成立事实；
- 用图中的公式替查数结果重新计算未经声明的指标；
- 让 LLM 生成或修改 Cypher；
- 以向量相似度结果直接覆盖精确绑定。

## 6. 跨场景分析约束

跨场景对象是分析契约，不是 SQL JOIN。只有同时满足以下条件才可以提供给主 Agent：

- 两端场景在当前用户授权范围内；
- 两端结果列分别命中对应 `ResultBinding`；
- 关系状态为 `confirmed`；
- 关联键、时间依据、时间对齐条件和粒度对齐条件齐全；
- `allowed_analyses` 包含本次问题所需分析，且没有触发 `forbidden_inferences`；
- 使用同一个 `ontology_snapshot_id`。

任一条件不满足，都应返回“存在候选关系但本次未启用”的 trace，不应静默合并结果。

## 7. 与现有代码的衔接

当前运行时召回仍在：

```text
packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/context.py
packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/analysis_service.py
```

这两个模块目前读取关系库快照里的 `prompt_projection_json`，不能假设已经存在 Neo4j reader。迁移建议：

1. 先实现 Neo4j reader 和同一输入的双读对照，不改变主 Agent 输出；
2. 对照记录精确绑定命中、分析规则数量、跨场景候选数量和未绑定列；
3. 通过验收后再让 `analysis_service` 选择 Neo4j reader；
4. 保留关系库 JSON 回退，直到至少一个完整发布周期验证通过。

## 8. 发布与环境注意事项

- `DATAMAN_NEO4J_ENABLED=0` 时，发布不会写 Neo4j，现有 JSON 召回仍可用。
- `DATAMAN_NEO4J_ENABLED=1` 时，构建快照会同步 Neo4j；同步失败返回 `ONTOLOGY_NEO4J_SYNC_FAILED`。
- 只有 Neo4j 中 `ready` 或 `active` 的快照可以激活；旧活动快照会变为 `superseded`。
- 重试同一 `snapshot_id` 会清理并重写该快照；如果原状态是 `active`，重写后仍保留 `active`。
- 不要在日志、trace、Cypher 参数或文档里输出 Neo4j 密码。
- 不要提交 `docker/neo4j/.env`。

## 9. 后续验收清单

- 同一结果列使用相同 `scene_id + semantic_key` 时，每次命中同一个 `ResultBinding`；
- 不同 `snapshot_id` 的同名指标不会互相命中；
- 未绑定字段不会因名称相似而生成指标；
- 候选跨场景关系不会进入默认分析；
- 权限过滤在 Neo4j 查询条件和图扩展两处都生效；
- 分析输出能回溯到对象、关系、来源场景版本和 `ontology_snapshot_id`；
- Neo4j 不可用时，JSON 回退路径仍能完成已有分析；
- 双读对照结果达到约定一致性后再切换默认读路径。
