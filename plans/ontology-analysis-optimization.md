# 面向查数后分析的本体建设与召回优化方案

## 1. 目标和边界

本方案针对 Datrix 的 AskData 场景。每个场景绑定一个数据对象，并维护数据字典和业务语义文档；场景 Agent 仍然根据字段信息生成查询，主 Agent 在查询完成后结合多个场景的结果进行分析。

本方案确定以下边界：

- Ontology 不参与场景选择、字段选择、SQL 生成或跨场景查询组合。
- Ontology 只在查询结果已经返回后提供业务解释、指标口径、结果比较限制和分析建议。
- 查询结果中的数据行是本轮分析的事实，Ontology 提供的是概念、指标和解释规则，不把全量业务数据复制成 Individuals。
- 多场景共享一份可发布的全局快照，但概念先在领域内归并，跨领域关系必须通过显式桥接确认。

目标是让系统能够回答：

1. 结果中的每一列对应哪个业务指标或分析维度。
2. 这个指标的含义、单位、粒度、时间口径和计算方式是什么。
3. 多个结果中的指标是否可以比较，比较时需要满足什么条件。
4. 当前结果应当应用哪些异常解释、趋势解释和口径提醒。
5. 每条解释来自哪些场景文档，是否已经确认，是否存在冲突。

## 2. 当前实现和主要问题

现有流程大致是：

```text
场景快照
  -> 读取 semantic_md，并拆分数据字典和业务语义
  -> LLM 逐场景抽取实体、指标、关系和分析规则
  -> LLM 将多个场景结果归并成全局 Markdown
  -> Markdown 编译为图谱和 prompt projection
  -> 发布 Ontology Snapshot
  -> 查询完成后按场景、结果列和问题词投影一小段本体上下文
  -> 主 Agent 生成最终分析
```

相关实现位于：

- 生成流程：`packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/llm_generator.py`
- 生成提示词：`packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/llm_prompts.py`
- 规则生成回退：`packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/generator.py`
- Markdown 编译：`packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/markdown.py`
- 结果召回：`packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/context.py`
- 主 Agent 结果上下文：`packages/dbgpt-app/src/dbgpt_app/openapi/api_v1/tools/ask_data.py`

当前实现的主要问题如下。

### 2.1 场景文档提供了事实，但没有形成稳定的分析契约

数据字典和业务语义文档能够说明字段、业务关系、口径和边界，但指标候选主要依赖 LLM 归纳或关键词猜测。指标缺少统一的聚合方式、统计粒度、时间依据、币种和可比性定义。

### 2.2 全局归并容易把同名概念错误合并

项目管理、财务、科研和资产场景可能都出现“项目”“金额”“负责人”等词。名称相同只能说明有语言相似性，不能证明它们是同一个业务概念。当前全局归并提示要求合并同义实体和指标，但领域范围不是概念身份的强约束。

### 2.3 跨场景候选不是已确认的业务关系

规则生成器会根据多个场景出现的相似 `id`、`code`、`no`、`key` 字段生成跨场景候选。这适合发现线索，但不能直接证明两个场景中的对象可以对齐、两个指标可以比较，也不能说明时间和统计粒度兼容。

### 2.4 当前召回依赖结果列 key，但结果列语义元数据不足

召回主要使用 `scene_id`、结果列 `key`、列类型和问题文本。`ResultColumn` 目前没有稳定携带指标 key、聚合方式、单位、时间粒度和统计粒度，导致结果列和本体指标之间容易退化为字符串匹配。

### 2.5 召回有过宽兜底和顺序不稳定问题

当前 `relevant_ontology_projection()` 计算了相关性分数，但选中节点时没有按分数排序；没有命中时还会退化为返回一批通用实体、指标和场景。这样会把无关语义放进主 Agent 上下文，也会让生成顺序影响召回结果。

### 2.6 生成本体和场景自动片段可能出现语义层级混合

发布快照时，Markdown 编译图会和场景快照自动生成的场景/指标片段合并。场景片段适合作为结果绑定索引的一部分，但不应当和已经审核的业务概念混成同等可信度的全局本体对象。

## 3. 目标模型

最终发布对象仍然可以称为 `Ontology Snapshot`，但内部划分为以下模块：

```text
Global Ontology Snapshot
├── Core Concepts             公共业务概念
├── Domain Modules            项目、财务、科研、资产等领域本体
├── Scene Bindings             结果列/语义键到指标和维度的映射
├── Analysis Rules             查数后的结果解释规则
├── Cross-domain Bridges      跨场景、跨领域的分析桥接
├── Evidence and Quality       来源、证据、确认状态、冲突和置信度
└── Retrieval Index            供结果召回使用的索引
```

### 3.1 公共概念和领域概念

公共概念只放跨领域稳定成立的内容，例如组织、人员、时间、地点、货币和度量单位。具体业务概念先进入领域命名空间：

```text
core.organization
core.person
core.time
core.money

project.project
project.contract
project.budget_execution_amount

finance.payment_transaction
finance.paid_amount
finance.payment_progress

research.research_project
asset.fixed_asset
```

`project.project` 和 `research.research_project` 可以有桥接，也可以共享 `core.organization`，但不能因为名称包含“项目”就自动合并。

### 3.2 面向分析的指标契约

每个全局指标建议至少具备以下字段：

| 字段 | 含义 |
| --- | --- |
| `id` | 稳定的领域命名空间 ID |
| `name` / `aliases` | 展示名称和用户常用说法 |
| `subject_entity` | 指标描述的业务对象 |
| `measure` | 被度量的事实或事件 |
| `aggregation` | `sum`、`avg`、`count`、`count_distinct`、`ratio` 等 |
| `formula` | 声明式公式；只引用已定义指标 |
| `unit` / `currency` | 单位和币种 |
| `grain` | 指标成立的统计粒度 |
| `time_basis` | 创建、签订、发生、确认、支付等时间依据 |
| `dimensions` | 可用于解释和拆分的维度 |
| `positive_direction` | 越高越好、越低越好、中性或取决于场景 |
| `comparability` | 可与哪些指标比较、比较条件是什么 |
| `interpretation` | 对结果的业务解释 |
| `anomaly_guidance` | 常见异常和分析建议 |
| `status` | `candidate`、`confirmed`、`deprecated`、`conflicted` |
| `provenance` | 来源场景、文档章节、证据引用和版本 |

例如“信息化项目预算执行金额”和“财务已支付金额”都可以是金额指标，但必须保留不同的 `measure`、`aggregation`、`grain` 和 `time_basis`。

### 3.3 场景绑定

场景绑定是结果召回的主要入口，不等同于业务概念本身。建议使用下面的结构：

```json
{
  "scene_id": "finance_payment",
  "scene_snapshot_id": "scene_snapshot_x",
  "semantic_key": "paid_amount",
  "result_key_patterns": ["sum_paid_amount", "paid_amount"],
  "metric_id": "finance.paid_amount",
  "label_aliases": ["已支付金额", "支付金额"],
  "aggregation": "sum",
  "unit": "CNY",
  "status": "confirmed",
  "evidence": ["业务语义文档#指标口径"]
}
```

结果列到指标的匹配顺序固定为：

```text
scene_id + result_key 精确匹配
  -> scene_id + semantic_key 精确匹配
  -> 标准化 key/label/alias 匹配
  -> 用户问题术语匹配
```

问题文本只作为补充证据，不能取代结果列和场景绑定。

### 3.4 跨场景分析桥接

跨场景桥接表达的是“已经返回的两个结果如何解释”，不表达 SQL join。建议用明确的关系类型：

| 类型 | 含义 |
| --- | --- |
| `same_subject` | 两个结果指向同一个业务对象 |
| `maps_to` | 一个场景概念映射到另一个场景概念 |
| `related_but_not_equivalent` | 相关但不能视为同一指标 |
| `contributes_to` | 一个指标是另一个业务结果的组成或影响因素 |
| `reconciles_with` | 可以在条件满足时做对账或差异分析 |
| `precedes` / `follows` | 业务事件存在先后关系 |
| `not_comparable` | 明确禁止直接比较 |

桥接契约至少包含：两个端点、共同业务对象、主数据映射、时间对齐、粒度对齐、允许的分析、禁止的推断、证据、确认状态和生效版本。

信息化项目经费执行和财务支付进度可以建模为：

```text
project.budget_execution_amount
  --related_but_not_equivalent / reconciles_with-->
finance.paid_amount
```

条件可能是：项目编码已经通过主数据映射、时间范围采用同一财年、两边都按项目粒度汇总。桥接还应明确“预算执行金额不等于支付金额”，不能直接把两者相除得到支付进度。

## 4. 场景文档改造

数据字典和业务语义文档继续是本体建设的原材料，但需要提供更稳定的结构。文档仍然服务于场景 SQL Agent，不要求用户维护另一份重复的本体文件。

### 4.1 数据字典增加分析所需字段

保留现有字段、类型和说明，增加可选列：

| 建议列 | 用途 |
| --- | --- |
| `semantic_key` | 场景内稳定语义键 |
| `business_name` | 面向用户的名称 |
| `business_role` | `identifier`、`dimension`、`measure`、`date`、`status` 等 |
| `unit` / `currency` | 单位和币种 |
| `grain` | 一行数据代表什么 |
| `time_basis` | 该字段对应的业务时间 |
| `null_semantics` | 空值代表未知、未发生、未同步还是零 |
| `aliases` | 同义词和常用问法 |
| `master_concept` | 可选的公共或领域概念 ID |
| `mapping_code_system` | 编码属于哪个主数据体系 |

这些列是结构化事实，优先级高于 LLM 的推断。

### 4.2 业务语义文档增加固定章节

建议场景编辑器提供模板，包含：

```markdown
## 所属领域
- domain_id: project
- domain_name: 项目管理

## 业务对象和数据粒度
- 主业务对象：信息化项目
- 一行数据代表：项目在财年内的经费执行汇总

## 指标口径
| 指标语义键 | 业务名称 | 计算口径 | 时间依据 | 可比性 | 证据/备注 |
| --- | --- | --- | --- | --- | --- |

## 结果解释规则
- 当执行金额下降时，应先检查时间范围和项目状态。
- 空金额表示未同步，不按零解释。

## 跨领域线索
- 可能与 finance.payment_transaction 通过项目主数据关联。
- 当前只提供候选线索，不能视为已确认关联。
```

`跨领域线索`只负责提供候选，不直接发布为已确认桥接。已确认的映射和桥接由全局本体管理页维护，以避免在多个场景文档中重复维护同一关系。

### 4.3 场景发布前检查

场景启用前增加文档质量检查：

- 数据字典字段是否都能映射到实际 Schema。
- 指标和维度是否有稳定 `semantic_key`。
- 指标是否说明单位、粒度和时间依据。
- 空值语义是否明确。
- 业务语义中的指标是否能对应到场景查询能力。
- 领域 ID 是否存在，公共概念 ID 是否合法。

这些检查不改变 SQL 生成逻辑，只提高本体生成和结果召回的输入质量。

## 5. 本体生成流程

### 5.1 原材料准备

生成入口不再只传一段合并后的 `semantic_md`，而是向生成器提供结构化来源：

```text
scene_id
scene_snapshot_id
domain_id
scene_name / description
data_dictionary_md
business_semantics_md
query_capabilities
source_hashes
```

查询能力中的指标和维度只用于确认场景绑定、聚合方式和结果 key，不用于让 Ontology 参与 SQL 规划。

### 5.2 单场景抽取

先用确定性解析提取字段、语义键、单位、类型和查询能力，再由 LLM 提取候选：

- 业务实体和领域概念。
- 指标及其解释。
- 指标的统计粒度和时间依据候选。
- 结果解释规则。
- 与公共概念或其他领域的关联线索。
- 每条候选的文档证据、来源章节和质量提醒。

LLM 不能凭空补充来源中不存在的事实。若无法确认，应输出 `candidate` 和 `unresolved_question`。

### 5.3 领域内归并

同一个领域内才进行默认的实体和指标归并。归并依据依次包括：稳定概念 ID、语义键、主数据映射、定义和证据。名称相似只能形成候选，不能单独触发合并。

### 5.4 跨领域桥接候选

领域内归并完成后，单独生成桥接候选。候选来源可以包括：

- 场景文档中的跨领域线索。
- 两边引用同一个公共概念或主数据体系。
- 指标的业务事件链具有明确的先后关系。
- 领域专家提供的映射。

桥接候选必须保留左右两端的证据。没有主数据映射、粒度或时间口径的候选只能进入待审核列表。

### 5.5 编译和质量检查

发布前分为四类检查：

1. **结构检查**：ID、引用、关系端点、公式依赖和循环引用。
2. **来源检查**：证据是否能在对应场景文档中命中。
3. **绑定检查**：场景语义键、结果 key、查询能力和场景快照是否一致。
4. **分析检查**：单位、粒度、时间依据、可比性和桥接条件是否完整。

发现冲突时保留冲突，不自动选择一个口径覆盖另一个口径。带有未解决冲突的对象可以保存为草稿，但不能进入默认分析召回。

### 5.6 生成结果的可信状态

每个对象都带有：

```text
candidate    候选，不能默认用于结论
confirmed    已审核，可进入默认召回
conflicted   存在口径冲突，只能带警告召回
deprecated   已废弃，不进入默认召回
```

## 6. 查数后的召回和分析流程

### 6.1 统一结果语义画像

当前 `ResultBundleBuilder` 和主 Agent 工具都在构造本体上下文，容易产生两套召回结果。应新增统一的 `OntologyAnalysisService`，由两处共同调用。

查询完成后，先为每个场景生成 `ResultSemanticProfile`：

```json
{
  "scene_id": "finance_payment",
  "scene_snapshot_id": "scene_snapshot_x",
  "grain": ["project", "month"],
  "time_grain": "month",
  "time_basis": "payment_date",
  "columns": [
    {
      "result_key": "sum_paid_amount",
      "type": "metric",
      "semantic_key": "paid_amount",
      "aggregation": "sum",
      "unit": "CNY",
      "label": "已支付金额"
    }
  ]
}
```

`ResultColumn` 和 `SceneResult` 需要补充这些元数据。查询服务可以从已执行的 `QuerySpec`、场景 Snapshot 和编译后的列信息中填充，不需要重新解析 SQL。

### 6.2 分层召回

召回顺序固定为：

```text
权限过滤
  -> 结果列精确绑定
  -> 当前场景内的实体、维度和指标
  -> 指标所属领域的分析规则
  -> 结果中同时出现的已确认指标关系
  -> 当前结果涉及的已确认跨领域桥接
  -> 公共概念
```

用户问题只用于对同一层候选排序，不用于绕过场景和权限边界。

### 6.3 召回排序

候选按稳定分数排序，建议优先级如下：

| 命中条件 | 建议权重 |
| --- | ---: |
| 场景和结果 key 精确绑定 | 100 |
| 场景和 semantic key 精确绑定 | 90 |
| 结果 label 或别名命中 | 60 |
| 指标所属实体被结果列命中 | 40 |
| 已确认指标关系连接到已命中指标 | 30 |
| 领域内问题词命中 | 10 |
| 候选或冲突对象 | 降权或不召回 |

分数相同时按对象 ID 稳定排序。删除“没有命中就返回一批通用节点”的宽泛兜底；没有已确认绑定时返回明确的无命中状态和结果元数据。

### 6.4 桥接召回条件

跨场景桥接只有在以下条件同时满足时才进入默认上下文：

- 桥接状态为 `confirmed`。
- 左右端点指标都出现在本次结果中。
- 结果场景属于桥接声明的场景。
- 主数据对象已完成映射，或结果中明确包含相同的公共业务 ID。
- 时间范围和统计粒度满足桥接条件。

如果条件不满足，可以召回一条限制提示，但不能召回“可比较”结论。

### 6.5 规则触发

`ResultAnalysisRule` 不再只有自然语言的 `trigger`，建议增加机器可判断条件：

```json
{
  "id": "rule.payment_vs_budget_basis",
  "trigger": {
    "type": "metric_pair_present",
    "metrics": ["project.budget_execution_amount", "finance.paid_amount"]
  },
  "guidance": "两者可能采用不同确认时点，先说明口径差异，不直接计算支付进度。",
  "severity": "warning",
  "status": "confirmed"
}
```

结果分析服务先根据结果画像触发规则，再把触发的规则交给主 Agent。未触发的规则不进入上下文。

### 6.6 提供给主 Agent 的上下文

不要只输出一段混合文本，建议同时提供结构化对象和渲染文本：

```json
{
  "ontology_snapshot_id": "onto_x",
  "matched": {
    "metrics": ["project.budget_execution_amount", "finance.paid_amount"],
    "dimensions": ["core.time", "core.organization"],
    "bridges": ["bridge.informatization_budget.finance_payment"],
    "rules": ["rule.payment_vs_budget_basis"]
  },
  "analysis_constraints": [
    "不得将预算执行金额直接等同于支付金额",
    "两边只有在同一财年、同一项目粒度下才可做差异分析"
  ],
  "unresolved": [],
  "evidence": []
}
```

渲染文本用于 Agent 阅读，结构化对象用于前端展示、审计和后续规则检查。

## 7. 前端改造

### 7.1 场景编辑页

对应 `web/components/ask-data/SceneEditorForm.tsx`：

- 增加“所属领域”字段，支持选择已有领域或新建领域候选。
- 数据字典编辑器增加结构化表头提示和字段质量检查。
- 业务语义文档提供固定章节模板，不强制用户编写完整 Ontology。
- 增加“本体原材料预览”：显示识别出的实体、指标、语义键、粒度、时间依据和未确认项。
- 显示“该文档同时用于场景查询和本体生成”，明确两者职责不同。
- 场景详情页增加“本体来源状态”：当前文档是否已进入草稿、是否已发布、最近一次生成版本和待处理冲突。

### 7.2 本体管理页

对应 `web/pages/ontology/index.tsx`，由单纯的 Markdown/图谱编辑页扩展为以下视图：

1. **总览**：领域数量、来源场景、已确认对象、候选对象、冲突和待审核桥接。
2. **领域视图**：按项目、财务、科研、资产查看实体和指标，避免全局平铺。
3. **指标目录**：展示聚合、单位、粒度、时间依据、可比性和来源证据。
4. **跨领域桥接**：左右端点、映射键、时间/粒度条件、允许分析、禁止推断和审核状态。
5. **分析规则**：触发条件、解释指导、适用指标和严重级别。
6. **来源和冲突**：按场景快照查看证据、版本差异和口径冲突。
7. **召回模拟器**：输入场景、结果列、粒度和问题，预览实际会提供给主 Agent 的上下文。

生成草稿弹窗应支持：

- 选择领域或场景范围。
- 选择 LLM 抽取或规则候选模式。
- 查看单场景抽取和全局归并的 Diff。
- 查看每个对象的来源证据。
- 只应用新增或受影响的对象，避免覆盖管理员已有修订。

发布按钮前增加检查结果：未确认桥接、冲突指标、缺失绑定、缺少单位/粒度/时间依据和无证据对象都应明确列出。

### 7.3 查数结果页

对应 `web/components/ask-data/ResultDisplay.tsx`：

- 在“结论”和“数据口径说明”之间增加“语义分析依据”卡片。
- 显示本次使用的 Ontology Snapshot、命中的指标和维度。
- 显示跨场景桥接及其条件，明确“可比较”与“禁止推断”。
- 显示触发的分析规则和证据来源。
- 未命中已确认语义时显示“本次回答未使用已确认的本体指标绑定”，避免用户误以为系统使用了本体。
- 对冲突或候选对象使用警告标签，不能和已确认对象使用同样的视觉样式。

### 7.4 运行详情页

对应 `web/pages/ask-data/runs/detail.tsx`：

- 展示 Ontology Snapshot ID。
- 展示每个结果列的绑定过程和命中方式。
- 展示召回对象、过滤原因、触发规则和桥接条件检查结果。
- 记录“无命中”“无可确认桥接”“粒度不兼容”等分析告警。

## 8. 后端接口和存储调整

### 8.1 生成来源接口

扩展 Ontology 来源对象，明确传递：

```text
domain_id
data_dictionary_md
business_semantics_md
query_capabilities
scene_snapshot_id
source_hashes
```

保留旧的 `semantic_md` 兼容路径，但新生成器不再把合并 Markdown 作为唯一输入。

### 8.2 快照内容

`OntologySnapshot` 增加：

```text
domain_modules
scene_bindings
bridges
analysis_rules
analysis_index
quality_summary
```

第一阶段可以继续使用 JSON 存在快照中，避免立即引入大量数据库表；稳定后再将桥接和绑定拆成可查询表。

### 8.3 统一召回服务

新增类似 `OntologyAnalysisService` 的服务，统一被以下位置调用：

- `ResultBundleBuilder`，用于结构化结果包。
- `ask_data.py` 的主 Agent 结果内容，用于最终回答上下文。
- 结果详情接口，用于前端解释和审计。

这样可以避免同一个查询在后端生成两份不同的本体上下文。

### 8.4 权限

沿用当前按授权场景过滤的规则，并增加桥接权限约束：

- 普通用户只能看到左右端点场景都授权的桥接。
- 包含未授权场景的桥接不能通过名称、证据或指标间接泄露。
- 无场景来源的全局公共概念只在其被授权指标绑定后进入上下文。

## 9. 分阶段实施

### Phase 1：结果绑定和召回修正

- 扩充 `ResultColumn` 和 `SceneResult` 的语义元数据。
- 建立 `scene_id + result_key/semantic_key -> metric_id` 索引。
- 统一 `ResultBundleBuilder` 和主 Agent 的召回服务。
- 修复分数排序、过宽兜底和数字字段误判为指标的问题。
- 增加结果页“语义分析依据”展示。

### Phase 2：指标契约和文档质量

- 扩充数据字典表头和业务语义模板。
- 增加领域、聚合、单位、粒度、时间依据和空值语义。
- LLM 生成改为结构化来源输入。
- 增加来源证据、确认状态和冲突检查。

### Phase 3：跨领域桥接

- 新增桥接候选、确认、废弃和版本管理。
- 建立主数据映射和覆盖率检查。
- 对项目管理、财务选择一个真实案例建立桥接。
- 只有已确认且满足时间/粒度条件的桥接进入默认召回。

### Phase 4：审核和召回可视化

- 本体页增加领域视图、桥接审核和召回模拟器。
- 运行详情增加召回追踪。
- 记录用户对解释的纠正反馈，用于后续修订和验收。

## 10. 验收标准

至少准备以下结果分析用例：

1. 单项目场景：正确召回项目指标解释、单位、粒度和异常规则。
2. 单财务场景：正确解释支付金额和支付进度，不把空值当零。
3. 项目管理 + 财务：有已确认桥接时，提示可比较条件和差异解释。
4. 项目管理 + 财务：没有主数据映射时，不生成直接比较结论。
5. 同名“项目”但不同领域：不自动合并概念。
6. 时间粒度不一致：年度预算和月度支付不能直接计算比例。
7. 单位或币种不一致：只提示不可直接比较，不自动换算，除非有确认的换算规则。
8. 结果列未绑定本体：明确报告无已确认语义绑定，并继续依据数据口径回答。
9. 普通用户访问多场景：未授权场景的指标和桥接不能进入上下文。
10. 场景文档发生新版本：本体草稿显示受影响对象和桥接，发布前必须重新审核。

建议记录以下指标：

- 结果列到已确认指标的精确绑定率。
- 召回上下文中的无关对象比例。
- 跨场景桥接的错误比较率。
- 分析规则触发的准确率。
- 人工对指标口径和解释结果的接受率。

## 11. 推荐的落地判断

本项目不需要先引入完整 OWL 推理体系。当前最有价值的改造是把现有“全局本体 Markdown + 图谱”升级为：

```text
领域化业务语义
+ 指标分析契约
+ 场景结果绑定索引
+ 已确认的跨领域分析桥接
+ 结果分析规则
+ 证据、状态和版本
```

这样既保留场景化查数架构，也能让多个场景共同建设一份全局语义资产；本体只在查数完成后参与分析，并且每条解释都能说明依据、适用条件和禁止推断的边界。

## 12. 优化开发方案

本开发按“先打通结果绑定和召回，再增加领域与桥接，最后完善审核和前端体验”的顺序进行。每个阶段都可以独立上线，旧场景文档和旧 Ontology 快照保持可读取。

### 12.1 开发原则

- 查询链路与本体链路解耦。本体改造不能改变现有场景 Agent 的 QuerySpec 和 SQL 生成行为。
- 先建立确定性绑定，再使用 LLM 做候选归纳。结果列和场景语义键的精确映射优先于文本相似度。
- 领域内先归并，跨领域只生成候选桥接；只有审核通过的桥接进入默认召回。
- 生成、审核、发布和召回都保留版本与来源，能够回溯一次回答使用的本体快照。
- 新字段均提供兼容默认值；旧文档可以继续运行，但会显示“旧格式/语义信息不完整”的质量提示。

### 12.2 迭代 0：基线、样例和接口冻结

目标是建立可重复验证的样例和当前行为基线，不修改产品行为。

任务：

1. 选择三个代表场景：信息化项目、财务支付、资产或科研中的一个。
2. 为每个场景保存当前数据字典、业务语义文档、已发布 Scene Snapshot 和典型问数结果。
3. 固定至少十个查数后分析用例，包括单场景指标解释、同名概念隔离、跨场景可比、时间粒度不一致、无绑定和无授权场景。
4. 记录当前召回的节点、边、规则、上下文长度和最终回答，作为优化前基线。
5. 冻结第一版接口字段和兼容策略，避免前后端同时改动时反复调整。

交付物：

- `plans/ontology-analysis-optimization.md` 中的样例和验收用例清单。
- 后端召回快照 fixture。
- 生成结果和召回结果的 JSON golden files。

### 12.3 迭代 1：场景原材料和指标绑定

目标是让数据字典、业务语义文档、查询能力和结果列形成稳定的结果绑定。

后端任务：

| 任务 | 文件落点 | 内容 |
| --- | --- | --- |
| 扩充结果列元数据 | `scene/ask_data/query/query_spec.py` | 增加 `semantic_key`、`aggregation`、`label`、`unit`、`source_field` 等可选字段 |
| 生成结果元数据 | `scene/ask_data/query_service.py` | 从 QuerySpec、Snapshot 和编译列填充结果列，不解析 SQL 文本推断指标 |
| 扩充场景来源 | `ontology/llm_schemas.py`、`ontology/service.py` | 单独传递数据字典、业务语义、领域和查询能力，保留 `semantic_md` 兼容读取 |
| 指标契约 | `ontology/llm_schemas.py`、`ontology/schemas.py` | 增加聚合、粒度、时间依据、单位、可比性、确认状态和来源字段 |
| 场景绑定索引 | `ontology/markdown.py`、`ontology/service.py` | 编译 `scene_id + result_key/semantic_key -> metric_id`，检测重复和无效绑定 |
| 证据校验 | `ontology/llm_validator.py` | 证据必须包含场景、文档区段和引用；未命中分级为 warning 或 error |

前端任务：

- `web/components/ask-data/SceneEditorForm.tsx` 增加领域选择和文档模板提示。
- 数据字典模板增加 `semantic_key`、业务角色、单位、粒度、时间依据和空值语义列。
- 业务语义模板增加“业务对象和数据粒度”“指标口径”“结果解释规则”章节。
- `web/pages/ask-data/scenes/detail.tsx` 增加原材料质量摘要和当前场景绑定状态。
- `web/utils/ask-data-semantic.ts` 解析和构建新增字段时保持旧 Markdown 可读。

验收重点：查询生成结果不变；一条结果列能够通过精确绑定找到正确指标；绑定失败时显示可解释的原因。

### 12.4 迭代 2：统一召回服务和查数后接入

目标是消除两套召回逻辑，让结果分析使用结构化、可解释、受限的本体上下文。

后端任务：

1. 新增 `scene/ask_data/ontology/analysis_service.py`，提供统一入口：

   ```python
   build_analysis_context(
       snapshot,
       results,
       question,
       authorized_scene_ids,
       is_platform_admin,
   )
   ```

2. 在服务内按以下顺序执行：权限过滤、结果画像构建、精确绑定、领域内扩展、规则触发、桥接检查、文本渲染。
3. 修改 `ResultBundleBuilder` 和 `ask_data.py`，都调用统一服务，不再各自调用不同的投影逻辑。
4. 修复 `ontology/context.py`：
   - 按分数和稳定 ID 排序后截断。
   - 删除“无命中时返回通用节点”的宽泛兜底。
   - 只有明确为 metric 的结果列才进入指标绑定；数字维度不能自动当作指标。
   - `label_key`、单位、聚合方式、时间粒度和统计粒度参与匹配。
   - 只扩展一层已确认的实体、指标关系和分析规则。
   - 结果没有已确认绑定时返回 `unmatched_columns` 和使用限制。
5. 结构化上下文增加 `matched_by`、`confidence`、`evidence`、`constraints` 和 `unresolved`。
6. 记录本次回答使用的 `ontology_snapshot_id`、匹配对象 ID 和召回规则版本。

前端任务：

- `web/components/ask-data/ResultDisplay.tsx` 增加“语义分析依据”卡片。
- 显示命中的指标、领域、绑定方式、口径限制、触发规则和来源证据。
- 对候选、冲突和无绑定对象使用不同状态标签。
- `web/pages/ask-data/runs/detail.tsx` 增加召回追踪面板。

验收重点：同一查询通过 ResultBundle 和主 Agent 工具得到相同的本体上下文；上下文中不出现未授权或无关对象；无绑定时 Agent 能明确说明依据不足。

### 12.5 迭代 3：领域模型和跨领域桥接

目标是支持项目管理、财务、科研、资产等场景共同建设，同时避免同名概念误合并。

后端任务：

| 任务 | 文件落点 | 内容 |
| --- | --- | --- |
| 领域模型 | `ontology/llm_schemas.py`、`ontology/schemas.py` | 增加 `DomainModule` 和命名空间校验 |
| 桥接模型 | 新增 `ontology/bridge_schemas.py` 或并入 `schemas.py` | 定义端点、关系类型、主数据映射、粒度、时间条件、允许/禁止分析、证据和状态 |
| 候选生成 | `ontology/llm_generator.py`、`ontology/generator.py` | 领域内归并后再生成桥接候选，不自动激活 |
| 桥接校验 | `ontology/llm_validator.py`、`ontology/service.py` | 校验端点、场景权限、映射键、时间和粒度条件 |
| 快照索引 | `ontology/repository.py`、`ontology/service.py` | 快照增加 domains、bindings、bridges、analysis_index 和质量摘要 |
| API | `api/ontology.py` | 增加候选列表、确认/驳回、合并/拆分和桥接校验接口 |

桥接生命周期：

```text
candidate -> in_review -> confirmed -> deprecated
                     \-> rejected
```

信息化项目经费执行与财务支付进度作为第一条真实桥接，必须同时提供：

- 项目管理系统项目编码到财务项目编码的映射来源。
- 两个指标的业务定义、时间依据和统计粒度。
- 可以做的分析和禁止直接推导的结论。
- 至少一组实际结果的对齐和差异检查。

前端任务：

- `web/pages/ontology/index.tsx` 增加领域筛选和桥接审核入口。
- 新增桥接详情组件，展示左右端点、映射键、时间/粒度条件、证据、冲突和状态。
- 支持确认、驳回、修改、废弃和查看版本 Diff。
- 生成草稿时区分“场景内对象”“领域归并对象”和“跨领域候选”。

验收重点：同名“项目”在不同领域保持独立；没有主数据映射的候选不能默认召回；已确认桥接在满足条件时能够被结果分析召回。

### 12.6 迭代 4：审核工作台、召回模拟和质量闭环

目标是让管理员可以判断生成内容和召回结果，而不是只看 Markdown 和图谱。

前端任务：

1. 本体页增加按领域查看实体、指标、桥接和分析规则的视图。
2. 增加“来源证据”侧栏，显示场景、文档类型、章节、引用片段和来源版本。
3. 增加“生成 Diff”，区分新增、变更、删除、冲突和受影响绑定。
4. 增加“召回模拟器”，输入场景、结果列、粒度、时间范围和问题，显示最终上下文。
5. 运行详情增加“为什么召回”和“为什么过滤”信息。
6. 允许管理员标记错误绑定、错误解释和缺少桥接，形成后续修订清单。

后端任务：

- 保存召回 trace，但只保存对象 ID、规则版本和必要元数据，不保存不必要的完整结果行。
- 为每个候选记录质量问题和处理状态。
- 提供按场景、领域和快照查询质量摘要的接口。

## 13. 接口草案

### 13.1 生成和预览

保留现有 `/draft/from-active-scenes`，扩展请求参数：

```json
{
  "expected_revision": 3,
  "apply": true,
  "mode": "llm",
  "domain_ids": ["project", "finance"],
  "scene_ids": ["informatization_project", "finance_payment"]
}
```

返回中增加：

```json
{
  "generation": {
    "scene_candidates": 12,
    "domain_objects": 8,
    "bridge_candidates": 3,
    "conflicts": 1,
    "unresolved_questions": 2
  }
}
```

### 13.2 桥接审核

建议增加以下接口：

```text
GET  /api/v1/ask-data/ontology/bridges
POST /api/v1/ask-data/ontology/bridges/{id}/review
POST /api/v1/ask-data/ontology/bridges/{id}/validate
GET  /api/v1/ask-data/ontology/bridges/{id}/evidence
```

审核请求应包含操作、备注和期望版本，避免多人编辑覆盖：

```json
{
  "action": "confirm",
  "expected_revision": 4,
  "comment": "已与项目主数据和财务负责人确认项目编码映射"
}
```

### 13.3 召回诊断

增加管理员和运行详情使用的诊断接口：

```text
POST /api/v1/ask-data/ontology/retrieval-preview
```

输入为场景、结果列、统计粒度和问题，返回匹配对象、匹配方式、过滤原因、触发规则和桥接条件。该接口不能执行 SQL，只读取已发布快照。

## 14. 测试开发方案

### 14.1 单元测试

- 指标契约和领域命名空间校验。
- 结果列精确绑定、别名绑定和无绑定。
- 召回分数排序和稳定截断。
- 单位、统计粒度和时间依据兼容性判断。
- 桥接状态、端点和权限过滤。
- 证据命中和来源版本校验。

### 14.2 集成测试

- 场景文档生成领域候选、指标和绑定。
- 多场景生成不把同名概念自动合并。
- 项目管理与财务桥接从候选到确认，再到结果召回。
- Scene Snapshot 更新后，本体草稿提示受影响对象。
- ResultBundle 和主 Agent 工具使用同一个召回结果。
- 未授权场景的实体、指标、桥接和证据都不进入上下文。

### 14.3 回归测试

- 旧版场景语义文档能够继续生成 QuerySpec。
- 旧版 Ontology Markdown 能够编译和发布。
- 没有 Ontology 快照时，查数流程和回答流程保持可用。
- LLM 不可用时，规则模式仍可生成候选草稿，但候选不能绕过审核成为 confirmed。

## 15. 发布、迁移和回滚

### 15.1 兼容迁移

- 旧 `OntologySnapshot` 读取时补默认 `domain_id=legacy`、`status=candidate` 和空 `analysis_index`。
- 旧场景文档不强制立即补齐新字段，生成器将缺少信息标为质量问题。
- 旧 `scene_binding` 可以迁移为新绑定结构，无法确定 `result_key` 的记录保留原 `semantic_key`，并降低置信度。
- 新召回器支持按配置切回旧投影函数，便于灰度验证。

### 15.2 灰度顺序

```text
只记录新召回结果，不改变回答
  -> 对管理员开启新召回上下文
  -> 对单个领域开启
  -> 开启已确认跨领域桥接
  -> 全量切换
```

灰度期间比较新旧召回的对象数、上下文长度、无关对象比例和人工接受率。

### 15.3 回滚条件

出现以下情况时回退到上一版快照或旧召回器：

- 结果列误绑定率明显增加。
- 未授权场景对象进入上下文。
- 跨领域桥接导致无证据的直接比较结论。
- 主 Agent 上下文长度明显增加并影响正常回答。
- 旧场景查询行为发生回归。

回滚只切换活动 Ontology Snapshot 或召回版本，不删除草稿、审核记录和新索引，便于修复后再次发布。

## 16. 第一批具体开发任务

建议先实现以下最小闭环：

1. 扩展 `ResultColumn`，从查询能力和 QuerySpec 填充 `semantic_key`、`aggregation`、`unit` 和 `grain`。
2. 新增统一 `OntologyAnalysisService`，让结果包和主 Agent 共用一次召回。
3. 修正召回排序、指标列判定和无命中兜底。
4. 在数据字典和业务语义文档模板中增加领域、粒度、时间依据和空值语义。
5. 为信息化项目与财务支付建立一条人工确认的分析桥接。
6. 在结果展示页显示命中的指标、桥接、限制和证据。
7. 用十个固定结果分析用例完成新旧召回对比。

完成这七项后，再扩展其他领域和自动桥接候选。这样可以先验证“场景原材料 → 结果绑定 → 分析召回 → 用户可解释展示”的完整链路，再增加更多本体元素。
