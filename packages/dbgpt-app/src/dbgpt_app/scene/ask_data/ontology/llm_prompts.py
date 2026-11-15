"""Prompt templates for analysis-oriented Ontology generation."""

from __future__ import annotations

import json

from .llm_schemas import (
    GlobalOntologySynthesis,
    OntologySceneSource,
    SceneOntologyExtraction,
)


def build_scene_extraction_prompt(source: OntologySceneSource) -> str:
    schema = json.dumps(
        SceneOntologyExtraction.model_json_schema(), ensure_ascii=False, indent=2
    )
    payload = source.model_dump(mode="json")
    return f"""你是企业数据分析本体架构师。
你将读取一个场景的语义 Markdown。
你的任务不是生成 SQL，也不是复述字段，而是从业务语义文档提取可用于“查数后业务分析”的本体候选，并使用数据字典建立候选与字段的对应关系。

必须遵守：
1. 只能基于输入文档。
2. 每个实体、指标、关系、分析能力必须给出证据 quote。
3. 不确定内容不要强行确认，写入 quality_warnings。
4. 不输出 Markdown，只输出 JSON。
5. 不包含 SQL、连接信息、密码、API key。
6. 指标只能提取自语义文档（business_semantics_md 或 semantic_md 的 YAML 指标定义）；不得根据数据字典字段名、字段类型、字段说明或 query_capabilities 新增、推导、猜测指标。语义文档没有定义的指标不得输出。
7. 不得自动创建比例、合计、均值等派生指标。只有语义文档明确写出该指标及其定义时才能提取；指标证据 quote 必须来自语义文档，不能来自数据字典。
8. 指标必须说明分析含义、常见解读、异常情况、推荐分析维度；来源没有明确说明的聚合、粒度、时间依据、单位和可比性必须留空或标为未确认，不能猜测。
9. Evidence.scene_id 必须等于输入 scene_id。
10. owner_entity_local_id 必须引用本 JSON 的 entities.local_id。
11. domain_id 必须照抄输入场景的 domain_id；未设置时输出 null。
12. data_dictionary_md 仅用于理解字段业务名称、含义、值域和质量，并为语义文档中已定义的指标寻找字段映射；business_semantics_md 用于提取业务对象、指标口径、时间与粒度、分析规则。优先引用语义文档原文证据。
13. query_capabilities 只用于辅助已有语义概念的结果列绑定；不得作为新增指标的来源。

输出 JSON Schema：
{schema}

输入场景：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def build_global_synthesis_prompt(
    extractions: list[SceneOntologyExtraction],
) -> str:
    schema = json.dumps(
        GlobalOntologySynthesis.model_json_schema(), ensure_ascii=False, indent=2
    )
    payload = [item.model_dump(mode="json") for item in extractions]
    allowed_scene_ids = sorted({item.scene_id for item in extractions})
    return f"""你将收到多个场景的本体抽取 JSON。
你的任务是合成全局业务本体，用于主 Agent 在查询结果返回后做业务分析。

必须完成：
1. 合并同义实体。
2. 仅合并单场景抽取中已经出现且语义文档有证据的同义指标；不得新增或推导指标。
3. 建立实体关系和指标关系。
4. 生成分析维度。
5. 生成跨场景分析路径。
6. 生成查询结果分析规则。
7. 对口径冲突写 conflict_note，不要掩盖。
8. 每个对象保留 source_scenes 和 evidence。
9. 不输出 Markdown，只输出 JSON。
10. scene_bindings 只能引用输入场景中出现过的 semantic_key。
11. 所有 source_scenes、scenes、Evidence.scene_id、scene_bindings.scene 必须来自 allowed_scene_ids。
12. 不要编造未输入的业务系统、场景或数据源；如果只有一个场景，cross_scene_analysis_paths 可以为空数组。
13. 名称相同不代表概念或指标相同；不同 domain_id 的对象不得归并。只有同领域且定义、单位、粒度和时间依据兼容时才可归并。
14. 有 domain_id 时，GlobalEntity 和 GlobalMetric 的 id 必须使用 domain_id 前缀（例如 project.contract、finance.paid_amount）；domain_id 为空时保持兼容的原 ID。
15. 每条跨场景路径必须恰好涉及两个不同场景且状态为 candidate。它是待审核的分析契约，不是可执行 JOIN；关系类型、业务映射依据、左右时间依据、时间和粒度对齐条件、允许分析、禁止推断及来源证据应尽量完整，无法从输入确认的字段留空并在 unresolved_questions 说明。
16. 仅凭同名字段或相似编码不得声称跨场景映射已成立。不同领域之间只能提出待审核关系，不能合并实体或指标，也不能给出默认可比结论。

allowed_scene_ids：
{json.dumps(allowed_scene_ids, ensure_ascii=False, indent=2)}

输出 JSON Schema：
{schema}

单场景抽取结果：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


__all__ = ["build_global_synthesis_prompt", "build_scene_extraction_prompt"]
