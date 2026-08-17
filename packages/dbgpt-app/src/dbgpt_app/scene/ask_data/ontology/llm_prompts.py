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
你的任务不是生成 SQL，也不是复述字段，而是提取这个场景可用于“查数后业务分析”的本体候选。

必须遵守：
1. 只能基于输入文档。
2. 每个实体、指标、关系、分析能力必须给出证据 quote。
3. 不确定内容不要强行确认，写入 quality_warnings。
4. 不输出 Markdown，只输出 JSON。
5. 不包含 SQL、连接信息、密码、API key。
6. 指标必须说明分析含义、常见解读、异常情况、推荐分析维度。
7. Evidence.scene_id 必须等于输入 scene_id。
8. owner_entity_local_id 必须引用本 JSON 的 entities.local_id。

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
2. 合并同义指标。
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

allowed_scene_ids：
{json.dumps(allowed_scene_ids, ensure_ascii=False, indent=2)}

输出 JSON Schema：
{schema}

单场景抽取结果：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


__all__ = ["build_global_synthesis_prompt", "build_scene_extraction_prompt"]
