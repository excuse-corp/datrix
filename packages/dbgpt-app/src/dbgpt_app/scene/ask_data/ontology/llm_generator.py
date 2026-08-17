"""LLM-backed analysis Ontology generator."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from .generator import _normalize_source
from .llm_prompts import build_global_synthesis_prompt, build_scene_extraction_prompt
from .llm_renderer import render_ontology_markdown_from_synthesis
from .llm_schemas import (
    GenerateFn,
    GlobalOntologySynthesis,
    OntologyGenerationResult,
    OntologySceneSource,
    SceneOntologyExtraction,
)
from .llm_validator import (
    OntologyLLMValidationError,
    validate_global_synthesis,
    validate_scene_extraction,
    validate_sources_have_semantic_md,
)


class OntologyLLMGenerationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class OntologyLLMGenerator:
    """Run LLM understanding first; render Markdown only after schema validation."""

    def __init__(self, generate: GenerateFn):
        self._generate = generate

    async def generate(
        self,
        sources: list[OntologySceneSource],
        *,
        revision: int,
    ) -> OntologyGenerationResult:
        validate_sources_have_semantic_md(sources)
        extractions: list[SceneOntologyExtraction] = []
        warnings: list[str] = []
        for source in sources:
            prompt = build_scene_extraction_prompt(source)
            extraction, scene_warnings = await self._generate_scene_extraction(
                prompt, source
            )
            warnings.extend(scene_warnings)
            extractions.append(extraction)

        prompt = build_global_synthesis_prompt(extractions)
        synthesis, global_warnings = await self._generate_global_synthesis(
            prompt, sources
        )
        warnings.extend(global_warnings)
        warnings.extend(
            f"未确认事项：{item}"
            for item in synthesis.unresolved_questions
            if str(item).strip()
        )
        markdown = render_ontology_markdown_from_synthesis(
            synthesis, revision=revision
        )
        return OntologyGenerationResult(
            markdown=markdown,
            synthesis=synthesis,
            mode="llm",
            source_count=len(sources),
            entity_count=len(synthesis.entities),
            metric_count=len(synthesis.metrics),
            relation_count=len(synthesis.relations),
            analysis_rule_count=len(synthesis.result_analysis_rules),
            warnings=sorted(set(warnings)),
            raw={
                "scene_extractions": [
                    item.model_dump(mode="json") for item in extractions
                ],
                "synthesis": synthesis.model_dump(mode="json"),
            },
        )

    async def _generate_scene_extraction(
        self,
        prompt: str,
        source: OntologySceneSource,
    ) -> tuple[SceneOntologyExtraction, list[str]]:
        allowed_scenes = [source.scene_id]
        current_prompt = prompt
        last_text = ""
        for attempt in range(2):
            text = await self._safe_generate(
                current_prompt, "ONTOLOGY_LLM_UNAVAILABLE"
            )
            last_text = text
            try:
                extraction = _parse_model_json(
                    text, SceneOntologyExtraction, "ONTOLOGY_LLM_INVALID_JSON"
                )
                return extraction, validate_scene_extraction(extraction, source)
            except (OntologyLLMGenerationError, OntologyLLMValidationError) as exc:
                if attempt >= 1 or not _retryable_generation_error(exc):
                    raise
                current_prompt = _repair_prompt(
                    prompt,
                    last_text,
                    exc,
                    allowed_scenes=allowed_scenes,
                    hint="单场景抽取只能引用当前输入场景。",
                )
        raise OntologyLLMGenerationError(
            "ONTOLOGY_GENERATION_VALIDATION_FAILED",
            "模型生成内容未通过校验。",
        )

    async def _generate_global_synthesis(
        self,
        prompt: str,
        sources: list[OntologySceneSource],
    ) -> tuple[GlobalOntologySynthesis, list[str]]:
        allowed_scenes = sorted(source.scene_id for source in sources)
        current_prompt = prompt
        last_text = ""
        for attempt in range(2):
            text = await self._safe_generate(
                current_prompt, "ONTOLOGY_LLM_UNAVAILABLE"
            )
            last_text = text
            try:
                synthesis = _parse_model_json(
                    text, GlobalOntologySynthesis, "ONTOLOGY_LLM_INVALID_JSON"
                )
                return synthesis, validate_global_synthesis(synthesis, sources)
            except (OntologyLLMGenerationError, OntologyLLMValidationError) as exc:
                if attempt >= 1 or not _retryable_generation_error(exc):
                    raise
                current_prompt = _repair_prompt(
                    prompt,
                    last_text,
                    exc,
                    allowed_scenes=allowed_scenes,
                    hint=(
                        "全局归并只能引用 allowed_scene_ids 中的场景；"
                        "不要新增未输入的业务系统或场景。"
                    ),
                )
        raise OntologyLLMGenerationError(
            "ONTOLOGY_GENERATION_VALIDATION_FAILED",
            "模型生成内容未通过校验。",
        )

    async def _safe_generate(self, prompt: str, code: str) -> str:
        try:
            return await self._generate(prompt)
        except OntologyLLMGenerationError:
            raise
        except OntologyLLMValidationError:
            raise
        except Exception as exc:
            raise OntologyLLMGenerationError(
                code, f"模型暂时不可用，未生成草稿：{exc}"
            ) from exc


def normalize_scene_sources(scene_sources: list[Any]) -> list[OntologySceneSource]:
    sources: list[OntologySceneSource] = []
    for source in scene_sources:
        normalized = _normalize_source(source)
        scene_id = str(normalized.get("scene_id") or "")
        if not scene_id:
            continue
        semantic_md = str(normalized.get("semantic_md") or "")
        sources.append(
            OntologySceneSource(
                scene_id=scene_id,
                snapshot_id=str(normalized.get("snapshot_id") or ""),
                revision_id=str(normalized.get("revision_id") or ""),
                semantic_md_hash=str(normalized.get("semantic_md_hash") or ""),
                semantic_md=semantic_md,
                name=str(normalized.get("name") or "") or None,
                description=str(normalized.get("description") or "") or None,
            )
        )
    return sorted(sources, key=lambda item: item.scene_id)


def _parse_model_json(text: str, model, error_code: str):
    try:
        payload = json.loads(_extract_json(text))
    except json.JSONDecodeError as exc:
        raise OntologyLLMGenerationError(
            error_code, f"模型输出格式不合法，请重试：{exc}"
        ) from exc
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise OntologyLLMGenerationError(
            "ONTOLOGY_LLM_SCHEMA_INVALID", f"模型输出字段不符合 schema：{exc}"
        ) from exc


def _extract_json(text: str) -> str:
    value = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", value, flags=re.DOTALL)
    if fenced:
        value = fenced.group(1).strip()
    if value.startswith("{") and value.endswith("}"):
        return value
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        return value[start : end + 1]
    return value


def _retryable_generation_error(
    exc: OntologyLLMGenerationError | OntologyLLMValidationError,
) -> bool:
    code = getattr(exc, "code", "")
    return code in {
        "ONTOLOGY_LLM_INVALID_JSON",
        "ONTOLOGY_LLM_SCHEMA_INVALID",
        "ONTOLOGY_LLM_REFERENCE_INVALID",
        "ONTOLOGY_LLM_EVIDENCE_INVALID",
    }


def _repair_prompt(
    original_prompt: str,
    previous_output: str,
    exc: OntologyLLMGenerationError | OntologyLLMValidationError,
    *,
    allowed_scenes: list[str],
    hint: str,
) -> str:
    return f"""{original_prompt}

上一次输出没有通过后端校验，请只返回修正后的 JSON，不要解释。

校验错误：
- code: {getattr(exc, "code", type(exc).__name__)}
- message: {str(exc)}

硬性约束：
- {hint}
- allowed_scene_ids: {json.dumps(allowed_scenes, ensure_ascii=False)}
- source_scenes、scenes、Evidence.scene_id 必须来自 allowed_scene_ids。
- scene_bindings.scene 也必须来自 allowed_scene_ids。
- 无法确认跨场景路径时，将 cross_scene_analysis_paths 设为空数组。
- 不要编造其他系统或场景。

上一次输出：
{_truncate(previous_output, 20000)}
"""


def _truncate(value: str, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...<truncated>..."


__all__ = [
    "OntologyLLMGenerationError",
    "OntologyLLMGenerator",
    "normalize_scene_sources",
]
