# 查数后全局本体分析上下文方案

> **实施状态（2026-11-17）**：查询结果字段血缘、受控 SQL 的结构化口径、主 Agent 查数后本体分析、Neo4j 定向读图、关系库 JSON 回退和 Neo4j/JSON 双读差异日志已实现。指标来源严格校验和当前草稿来源清理已完成。双读对照默认关闭；Neo4j 仍按部署配置启用，默认关闭。

## 1. 目标和边界

查数成功后，用本次查询的最少必要元数据从已发布的全局本体中取得业务解释和分析约束，并与真实查询结果一起交给分析模型。

- **SQL 编排只负责查数**：选择场景、执行 SQL、收集结果和查询元数据，不读取本体、不生成本体上下文。
- **本体读取属于查数后的主 Agent 分析阶段**：由服务端 `OntologyAnalysisService` 代表该阶段读取 Neo4j，再把精简语义上下文与查询结果一起交给主 Agent。LLM 不直接连接 Neo4j。
- 本体只参与查数后的分析，不参与场景路由、字段选择或 SQL 生成。
- Neo4j 保存和提供已发布本体图；关系库 JSON 快照在迁移期间作为对照和回退来源。
- 指标只能来自语义文档明确声明或描述的内容。字段和数据字典只用于匹配已声明的语义，不产生新指标。
- 不把 SQL 交给 LLM 生成图查询；图查询由固定、参数化的 Cypher 实现。

## 2. 查数侧提供的最小输入

| 信息 | 最少字段 | 用途 |
| --- | --- | --- |
| 用户问题 | `question` | 确定本次需要解释的业务问题 |
| 场景版本 | 每个结果的 `scene_id`、`scene_snapshot_id` | 将本次数据与本体中对应的场景版本对齐 |
| 结果列 | `key`、`semantic_key`、`type`、`source_field`、`aggregation`、`unit` | 将实际返回的指标和维度匹配到显式语义绑定 |
| 实际字段血缘 | 稳定的 `field_ref` 和使用位置 | 标记执行查询实际使用的字段及其用途，如输出/聚合、筛选、分组、时间条件；有 JOIN 时再记录关联字段 |
| 分析口径 | `time_range`、语义化的 `time_basis`、`time_grain`、`grain` | 判断本次结果的统计范围和粒度，检查跨场景条件 |
| 用户权限 | 主 Agent 请求对应的服务端 principal | 限定可参与本次分析的场景及其对象、关系；不从用户输入读取 |

实际查询结果行单独交给主 Agent；本体分析服务接收查询元数据和服务端权限上下文，不需要原始 SQL 或场景全部字段清单。`field_ref` 必须限定在场景及其快照内，不能只凭跨场景可能重复的字段名匹配。普通单场景查询没有 JOIN 时，不必传空的 JOIN 明细。

本体分析服务在查数完成后读取一次当前活动的全局本体快照，固定其 `ontology_snapshot_id`，并将该 ID 放入返回的本体上下文和分析审计信息。它不是查数编排器的输入，也不要求 SQL 查询阶段读取或固定全局本体。

推荐输入形态：

```json
{
  "question": "分析项目经费执行与支付进度",
  "scenes": [
    {
      "scene_id": "project",
      "scene_snapshot_id": "scene_snapshot_...",
      "result_columns": [
        {
          "key": "executed_amount",
          "semantic_key": "project.executed_amount",
          "type": "metric",
          "source_field": "...",
          "aggregation": "sum",
          "unit": "元"
        }
      ],
      "field_lineage": [
        {"field_ref": "project@scene_snapshot_...:...", "usage": ["aggregate"]}
      ],
      "time_range": {"start": "...", "end_exclusive": "...", "timezone": "..."},
      "time_basis": "...",
      "time_grain": "month",
      "grain": ["project.department"]
    }
  ]
}
```

principal 和允许访问的场景由服务端单独传给本体分析服务，不放入用户可控的查询结果 JSON。查询失败或部分成功的场景应保留状态，不能作为已取得结果的一侧参与跨场景分析。

## 3. 查数侧现状核对

| 所需信息 | 当前状态 | 结论 |
| --- | --- | --- |
| 用户问题 | QuerySpec 编排和主 Agent 工具调用中都有问题文本 | 可提供；统一使用本次原始问题或规范化后的问题 |
| 场景和场景快照 | `SceneResult` 有 `scene_id`、`snapshot_id`、`scene_revision` | 可提供；传入本体时将 `snapshot_id` 命名为 `scene_snapshot_id` 以免与全局本体快照混淆 |
| 结果列 | `ResultColumn` 有 key、类型、语义键、来源字段、聚合和单位 | 部分可用；QuerySpec 路径通常可以填全，受控 SQL 路径对别名或复杂表达式可能缺少 `source_field` |
| 实际字段用途 | `SceneResult.field_lineage` 已由 QuerySpec 编译器和受控 SQL AST 解析填充 | 可提供；字段引用限定到 `scene_id + scene_snapshot_id + source_field`，记录输出、聚合、分组、筛选、时间筛选、JOIN、HAVING、排序用途；无法确认来源时标为未解析 |
| 时间和粒度 | QuerySpec 路径写入 `grain`、`time_grain`、`time_range`；当前 `time_basis` 填的是配置的物理时间字段 | 范围和粒度基本可用；物理字段不能直接当作业务时间依据 |
| 时间和粒度（受控 SQL） | `execute_sql()` 从最终执行 SQL AST 提取分组、日期截断粒度和可确定的时间边界 | 可提供；无法精确确定的值保持未知；业务时间依据只使用语义文档明确名称，不把物理字段名当作业务依据 |
| 用户权限 | 主 Agent 工具调用持有服务端 principal 和场景授权范围 | 本体分析阶段可直接使用；不得信任请求体或 LLM 生成的授权范围 |
| 全局本体版本 | SQL 编排器和 `ResultBundleBuilder` 不读取本体；主 Agent 查数后分析阶段通过 `analysis_snapshot_with_source()` 读取一次活动快照 | 已解耦；上下文携带本体快照 ID和读取来源 |

因此，查数侧当前已经提供召回所需的必要元数据。语义文档未明确声明的业务时间依据、复杂 SQL 无法精确解析的时间范围等仍会留空；系统不会用字段名或展示文案推测。

主 Agent 当前把生成后的 SQL 通过 `sql_by_task` 交给编排器，执行时走 `SceneQueryService.execute_sql()`；所以受控 SQL 路径是本次改造必须优先覆盖的主路径。QuerySpec 编译路径仍需维持同一输出结构，避免两种调用方式产生不同的召回输入。

### 两种执行路径的补充方式

1. **QuerySpec 编译路径**：编译器在解析业务 key 到字段定义时，直接记录被实际选用的字段及用途：`select/aggregate`、`group_by`、`filter`、`time_filter` 和需要时的 `order_by`。来源是已验证的 QuerySpec 和场景能力映射，不需要从生成后的 SQL 猜测。
2. **受控 SQL 路径**：对最终执行的 SQL 使用现有 `sqlglot` 解析结果 AST，记录 SELECT/聚合、WHERE、GROUP BY、HAVING、时间条件及 JOIN 字段的用途；将 SELECT 别名解析回源字段。只把结构化血缘放进结果，不把原始 SQL 发给图查询或分析上下文。
3. **公共结果模型**：新增 `field_lineage` 结果字段，两条路径都填同一结构。建议字段引用包含 `scene_id`、`scene_snapshot_id`、字段标识和用途。解析不到、存在歧义或表达式不支持时记录 `unresolved`，不根据名称推断语义。
4. **结构化口径**：QuerySpec 路径继续使用规范化输入；受控 SQL 路径将可确定的分组、时间范围、实际时间字段和粒度填入统一字段。物理 `time_field` 通过场景中明确配置的时间定义映射为业务 `time_basis`；只有精确匹配时才填写，无法确认则留空，不把展示文本反解析成精确条件。
5. **职责和版本固定**：SQL 编排结果不包含本体上下文或 `ontology_snapshot_id`。查数后的本体分析服务只读取一次活动快照，并把读取到的 `ontology_snapshot_id` 和生成的上下文交给主 Agent；同一回答过程不再重新读取活动版本。

Neo4j 当前的 `ResultBinding` 主要保存 `scene_id`、`scene_snapshot_id`、`semantic_key` 和目标对象。后续应补充经数据字典或语义文档明确记录的精确 `field_ref` 映射。没有明确映射时保留未映射状态，不以字段名称相似度自动确认指标。

## 4. 查数后的本体分析步骤

```text
SQL 编排执行场景查询
        ↓
返回查询结果 + 最小查询元数据（不含本体）
        ↓
主 Agent 查数后分析阶段调用 OntologyAnalysisService
        ↓
读取一次当前活动的全局本体快照并固定 ontology_snapshot_id
        ↓
按场景快照和语义键精确匹配 ResultBinding
        ↓
必要时按已登记的 field_ref 精确匹配绑定
        ↓
沿白名单关系取得业务对象、解释规则和分析约束
        ↓
校验权限、绑定状态、场景版本及跨场景时间/粒度条件
        ↓
返回紧凑的本体分析上下文和未匹配项
```

匹配顺序固定如下：

1. 先按 `ontology_snapshot_id + scene_id + scene_snapshot_id + semantic_key` 找显式绑定。
2. 结果列没有语义键时，只能按本体中已登记的精确 `field_ref` 映射匹配。
3. 名称或别名可以作为未确认候选展示，不能自动成为指标绑定。
4. 未命中列保留为 `unmapped`，由分析模型按查询结果和数据口径谨慎说明。

图扩展仅取已命中对象直接相关的业务定义、适用分析规则/维度及分析路径，并限制关系类型、扩展层数和返回规模。跨场景路径只有在状态已确认、两侧结果都存在且用户有权限、时间依据/范围/粒度均满足契约时，才作为本次可用的分析依据；否则只返回未满足条件及原因。分析服务把本体上下文和固定的 `ontology_snapshot_id` 交给主 Agent，随后主 Agent 结合原查询结果形成最终回答。

## 5. 接入查数后的分析流程

SQL 编排器只构造查询结果包。主 Agent 进入查数后分析阶段时，调用服务端 `OntologyAnalysisService` 接收查询元数据和 principal，再由它调用 Neo4j reader。召回结果与实际查询结果分别保留，最终一起提供给主 Agent：

```text
SQL 编排器：查询执行 → 查询结果包（结果行 + 最小查询元数据）
                                      ↓
主 Agent 分析阶段：OntologyAnalysisService → 本体分析上下文
                                      ↓
                      主 Agent：查询结果 + 本体上下文 → 最终分析
```

返回给分析模型的本体上下文只包含：本体快照 ID、命中绑定和对象、必要的定义/规则/分析约束、跨场景条件检查结果、未匹配列。原始查询 SQL、数据库连接信息和无关场景材料不进入分析上下文。

`ResultBundleBuilder` 只整理查数结果；本体上下文在主 Agent 查数后分析阶段生成一次，并包含该次读取的 `ontology_snapshot_id`。`ResultBundle` 中的旧 `ontology_context` 字段保留兼容，但 SQL 编排不再填充。

## 6. 实施阶段

1. **解除职责耦合**：已完成。`ResultBundleBuilder` 只构造查询结果，SQL 编排器不读取活动本体。
2. **补齐共同查询元数据**：已完成。两条查询路径均填充统一的 `field_lineage`，受控 SQL 路径提取可确定的时间/粒度字段。
3. **补齐本体显式映射**：已完成新快照的字段引用材料和 Neo4j `ResultBinding` 定向匹配；缺少字段引用时不猜测。
4. **主 Agent 分析阶段一次性读取**：已完成。服务端按场景、场景快照、语义键或字段引用从 Neo4j 读取，再做白名单扩展和权限裁剪；失败时按配置回退 JSON。
5. **双读验证**：已完成影子对照能力。设置 `DATAMAN_NEO4J_COMPARE_JSON=1` 后，同一活动快照、结果列、血缘和权限范围会分别生成 Neo4j 与 JSON 分析投影；结构化日志记录快照是否一致、整体是否等价及各投影部分的差异数量，不记录业务字段值。该检查默认关闭，比较失败不会影响主分析。
6. **指标来源治理**：已完成。发布校验只接受来源场景语义文档 `metrics` 配置中明确声明的指标；没有明确声明的指标不能进入本体。已提供草稿来源清理接口，清理时同步删除相关场景绑定和指标关系，不修改已激活快照。

## 7. 验收标准

- 同一场景快照、语义键和本体快照始终命中相同绑定。
- QuerySpec 与受控 SQL 两条路径均能记录实际使用的输出、筛选、分组和时间字段；SQL 有 JOIN 时记录关联字段；字段映射只认显式依据。
- 无法解析或无法确认的 SQL 字段、时间范围和粒度明确标为未知，不填入推测值。
- 无映射字段保持未匹配，不会因字段名称或相似度变成指标。
- 不同场景或不同场景快照中的同名字段不会串配。
- 未授权场景对象及关系不会进入结果上下文。
- 跨场景路径只有在已确认且两侧结果、时间范围和粒度符合条件时才可用于分析。
- SQL 编排结果不包含 `ontology_context`，也不依赖全局本体服务。
- 主 Agent 查数后分析阶段只读取一次活动本体快照，并记录该次使用的 `ontology_snapshot_id`。
- 查询结果、命中对象、未匹配项和本体上下文可通过同一查询标识追踪；该 trace 不由 SQL 编排器生成。
- Neo4j 不可用时可按配置回退到现有 JSON 快照。
- 设置 `DATAMAN_NEO4J_COMPARE_JSON=1` 时可在日志中核对 Neo4j 与 JSON 的分析投影差异；关闭时不执行影子读取。

## 相关实现位置

- 查询结果模型：[`query_spec.py`](../packages/dbgpt-app/src/dbgpt_app/scene/ask_data/query/query_spec.py)
- SQL 解释和口径：[`sql_explanation.py`](../packages/dbgpt-app/src/dbgpt_app/scene/ask_data/result/sql_explanation.py)
- 结果包构建：[`builder.py`](../packages/dbgpt-app/src/dbgpt_app/scene/ask_data/result/builder.py)
- 本体分析服务：[`analysis_service.py`](../packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/analysis_service.py)
- 当前 JSON 选择逻辑：[`context.py`](../packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/context.py)
- Neo4j 图存储：[`neo4j_store.py`](../packages/dbgpt-app/src/dbgpt_app/scene/ask_data/ontology/neo4j_store.py)
