# Datrix 三层本体结构

本文说明 Datrix 全局本体包含哪些层、每层有哪些对象和关系，以及对象如何记录来源和版本。三层是同一张本体图中的逻辑分类，不是三个互不相关的本体。

## 总体结构

| 层 | 主要回答 | 典型对象 |
| --- | --- | --- |
| 业务语义层 | 业务中有哪些对象、指标和概念，它们如何关联？ | 实体、指标、维度、业务关系 |
| 分析解释层 | 业务对象和指标应如何解释，有哪些适用条件和限制？ | 分析规则、分析维度、分析路径、澄清规则、对齐条件 |
| 映射治理层 | 本体对象来自哪些场景材料，映射依据和审核状态是什么？ | 场景、场景快照、结果绑定、证据、来源文档、审核记录 |

一个概念可以通过关系连接不同层。例如，场景快照声明一个结果绑定，结果绑定映射到指标；指标再关联到适用的分析规则。层用于区分对象职责，关系负责表达它们之间的联系。

## 业务语义层

业务语义层描述领域中的业务概念及其业务关系。

| 对象 | 含义 | 常见属性 |
| --- | --- | --- |
| `Entity` 实体 | 业务中可识别、可描述的对象，如项目、合同、人员、部门 | ID、名称、别名、定义、领域、来源场景 |
| `Metric` 指标 | 有明确业务含义和统计口径的度量，如合同金额、支付金额 | ID、名称、定义、单位、聚合方式、粒度、时间依据、公式、来源 |
| `Dimension` 维度 | 用于描述或划分业务对象和指标的属性，如项目类别、所属部门 | ID、名称、适用实体或指标、说明 |
| 业务关系 | 描述实体、指标和维度之间的业务联系 | 关系类型、两端对象、说明、证据 |

常见关系包括：

```text
Metric  -[:BELONGS_TO]->        Entity
Entity  -[:RELATED_TO]->        Entity
Metric  -[:RELATED_TO_METRIC]-> Metric
Metric  -[:USES_DIMENSION]->    Dimension
```

领域标识用于区分不同业务范围。名称相同不代表对象相同；例如项目领域和科研领域中的同名概念，应依据定义和来源分别建模，不能只按名称合并。

## 分析解释层

分析解释层保存对业务语义的解释、适用范围和判断限制。

| 对象 | 含义 | 主要内容 |
| --- | --- | --- |
| `AnalysisRule` 分析规则 | 对特定实体或指标给出的解释规则 | 触发条件、解释指导、适用对象、状态 |
| `AnalysisDimension` 分析维度 | 描述某指标或实体适合采用的分析角度 | 适用指标或实体、分析用途、注意事项 |
| `AnalysisPath` 分析路径 | 表达不同业务对象或指标之间的分析关系 | 涉及对象、关系主题、适用条件、分析限制 |
| `ClarificationRule` 澄清规则 | 说明语义不明确时需要补充确认的内容 | 澄清条件和需要确认的问题 |
| `AlignmentCondition` 对齐条件 | 描述跨对象分析需要满足的对齐前提 | 时间依据、时间范围、统计粒度等条件 |

常见关系包括：

```text
AnalysisRule      -[:APPLIES_TO_METRIC]-> Metric
AnalysisRule      -[:APPLIES_TO_ENTITY]-> Entity
AnalysisDimension -[:APPLIES_TO_METRIC]-> Metric
AnalysisPath      -[:FROM_METRIC]->       Metric
AnalysisPath      -[:TO_METRIC]->         Metric
AnalysisPath      -[:REQUIRES]->          AlignmentCondition
```

跨场景分析路径描述业务上的关联及其条件。路径本身不等于数据已经对齐，也不表示两个对象可以无条件比较；其适用范围、限制和状态都应保留在结构中。

## 映射治理层

映射治理层记录本体对象与场景版本、语义键及其来源证据之间的联系。

| 对象 | 含义 | 主要内容 |
| --- | --- | --- |
| `Scene` 场景 | 提供业务语义和数据定义的场景 | 场景标识、名称、来源信息 |
| `SceneSnapshot` 场景快照 | 某个已发布场景版本的来源记录 | 场景 ID、快照 ID、修订 ID、语义文档摘要 |
| `ResultBinding` 结果绑定 | 场景中的语义键与本体对象之间的显式映射 | 场景 ID、快照 ID、语义键、目标对象、状态 |
| `Evidence` 证据 | 支持指标或关系的原始材料摘录 | 场景、文档区段、证据文本、来源对象 |
| `SourceDocument` 来源文档 | 场景语义文档的版本化来源记录 | 场景、快照、文档类型、文档摘要 |
| `ReviewRecord` 审核记录 | 对对象或关系的审核过程记录 | 审核对象、审核状态、审核人和时间等治理信息 |

常见关系包括：

```text
Scene         -[:HAS_SNAPSHOT]->     SceneSnapshot
SceneSnapshot -[:DECLARES_BINDING]-> ResultBinding
ResultBinding -[:MAPS_TO]->          Metric|Dimension|Entity
Metric        -[:SUPPORTED_BY]->     Evidence
Evidence      -[:FROM_DOCUMENT]->    SourceDocument
ReviewRecord  -[:REVIEWS]->          被审核对象
```

场景绑定和结果绑定应有明确的语义键及目标对象。缺少映射依据的字段不能仅凭字段名称被视为某个指标。

## 来源和定义边界

- 语义文档是业务定义和指标定义的来源。全局指标应保留来源场景、场景级指标标识、文档区段和证据。
- 数据字典描述字段及其属性，可以补充已声明指标的字段映射、单位、粒度和时间依据；它不能单独创建业务指标。
- 查询能力、物理字段名和字段类型不构成新增指标的依据。
- 同名对象、相似字段或跨场景共现本身不构成对象等价或指标可比的证据。
- 来源场景快照和对象状态应随对象或关系保存，以便区分来源版本及候选、确认等治理状态。

## Neo4j 中的快照图结构

全局本体发布到 Neo4j 时，使用快照隔离不同版本：

```text
(:OntologySnapshot)-[:CONTAINS]->(:OntologyObject)
```

`OntologySnapshot` 保存本体版本、内容哈希、状态和对象/关系统计。每个本体对象以 `OntologyObject` 为基础标签，并可附加 `Entity`、`Metric`、`AnalysisRule`、`SceneSnapshot`、`ResultBinding` 等类型标签。对象带有 `object_id`、`object_type`、`layer`、`snapshot_id`、来源和扩展属性。

本体关系采用固定关系类型，并带有 `relation_id`、`relation_type`、`layer`、`snapshot_id`、来源和状态等属性。`CONTAINS` 只表示对象属于某个发布快照，不是业务语义关系。

关系型元数据库保存 Markdown 草稿、修订和管理状态；Neo4j 保存发布后的图快照。发布图由兼容现有本体结构的编译结果规范化而来，三层信息通过对象和关系的 `layer` 属性标识。

## 当前结构实现说明

上文列出的是三层结构的对象范围和关系语义。代码当前沿用统一的 `OntologyGraph` 节点/关系模型，在发布时按类型标记 `layer`，再写入 Neo4j；这不代表每种概念都已有独立的编辑表单或完整字段。

目前发布编译会从来源场景快照创建 `Scene`、`SceneSnapshot` 和 `SourceDocument`；只有图中存在带语义键的显式 `scene_binding` 时才创建 `ResultBinding`；全局指标带 `source_metric_refs` 时会创建 `Evidence`。`ReviewRecord`、独立 `Dimension` / `Unit` / `TimeBasis` 对象及更多治理字段是结构可以承载的对象类型，当前还没有完整的编辑和生成流程。

当前 Neo4j 图上的主要分类为：

| 层 | 当前主要节点类型 | 当前主要关系类型 |
| --- | --- | --- |
| 业务语义层 | `entity`、`metric` | `relation`、`belongs_to`、`metric_relation` |
| 分析解释层 | `rule`、`analysis_dimension`、`analysis_path`、`analysis_rule` | `cross_scene_join`、`clarification_rule`、`applies_to_metric`、`applies_to_entity`、`from_metric`、`to_metric`、`requires` |
| 映射治理层 | `scene`、`scene_snapshot`、`result_binding`、`evidence`、`source_document`、预留的 `review_record` | `scene_binding`、`provided_by`、`has_snapshot`、`declares_binding`、`maps_to`、`supported_by`、`from_document`、`reviews` |

落到 Neo4j 后，对象节点统一使用 `OntologyObject` 基础标签，已知类型再附加类型标签；每条对象及关系都带 `snapshot_id` 和 `layer`。因此这里的“层”首先是统一图模型中的分类属性，具体节点标签和关系类型用于表示对象类型及关联类型。

结构规范化实现见 [`graph_model.py`](../packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/graph_model.py)，Neo4j 标签、约束和关系存储实现见 [`neo4j_store.py`](../packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/neo4j_store.py) 与 [`neo4j_schema.py`](../packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/neo4j_schema.py)。
