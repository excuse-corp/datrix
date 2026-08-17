"""Fail-closed deterministic RouterAgent over routing projections only."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable

from ..schemas.plan import CombinePlan, MainAgentPlan, PlanTask
from ..schemas.snapshot import Snapshot
from ..snapshot.registry import RegistryLookupError, SceneAgentRegistry


class RouterAgent:
    max_tasks = 10

    def __init__(
        self,
        registry: SceneAgentRegistry,
        ontology_snapshot_provider: Callable[[], object | None] | None = None,
    ):
        self.registry = registry
        self.ontology_snapshot_provider = ontology_snapshot_provider

    def route(self, question: str) -> MainAgentPlan:
        normalized = question.strip().lower()
        if not normalized:
            return MainAgentPlan(
                action="reject",
                reason_code="EMPTY_QUESTION",
                message="Question is empty",
            )
        if self._contains_injection(normalized):
            return MainAgentPlan(
                action="reject",
                reason_code="INVALID_PLAN",
                message="Question contains an unsafe instruction",
            )
        ontology_plan = self._ontology_plan(question)
        if ontology_plan is not None:
            return ontology_plan
        candidates = []
        self.registry.refresh()
        for snapshot in self.registry.active_snapshots():
            scene_id = snapshot.scene_id
            projection = snapshot.routing_projection
            haystack = " ".join(
                [
                    str(projection.get("name", "")),
                    str(projection.get("description", "")),
                    " ".join(projection.get("keywords", [])),
                ]
            ).lower()
            direct_terms = self._direct_scene_terms(projection)
            direct_matches = {term for term in direct_terms if term in normalized}
            token_matches = {
                token for token in self._tokens(normalized) if token in haystack
            }
            if direct_matches or token_matches:
                candidates.append((scene_id, snapshot, direct_matches))
        if not candidates:
            return MainAgentPlan(
                action="reject",
                reason_code="NO_MATCHING_SCENE",
                message="No active Scene matches the question",
            )
        explicit_multi = [item for item in candidates if item[2]]
        if len(explicit_multi) > self.max_tasks:
            return MainAgentPlan(
                action="reject",
                reason_code="TOO_MANY_SCENES",
                message=f"At most {self.max_tasks} Scenes can run in one request",
            )
        if len(explicit_multi) > 1 and self._requests_multi_scene(normalized):
            return self._execute_plan(
                question,
                [(scene_id, snapshot) for scene_id, snapshot, _ in explicit_multi],
            )
        if len(candidates) > 1:
            return MainAgentPlan(
                action="clarify",
                clarification={
                    "question": "请选择要查询的业务场景",
                    "field": "scene_id",
                    "options": [scene_id for scene_id, _, _ in candidates[:5]],
                },
            )
        scene_id, snapshot, _ = candidates[0]
        return self._execute_plan(question, [(scene_id, snapshot)])

    def route_scene(self, question: str, scene_id: str) -> MainAgentPlan:
        """Build a plan pinned to one Scene, bypassing main-scene routing."""
        normalized = question.strip().lower()
        if not normalized:
            return MainAgentPlan(
                action="reject",
                reason_code="EMPTY_QUESTION",
                message="Question is empty",
            )
        if self._contains_injection(normalized):
            return MainAgentPlan(
                action="reject",
                reason_code="INVALID_PLAN",
                message="Question contains an unsafe instruction",
            )
        try:
            snapshot = self.registry.get(scene_id)
        except RegistryLookupError:
            return MainAgentPlan(
                action="reject",
                reason_code="SCENE_NOT_ACTIVE",
                message=f"Scene is not active: {scene_id}",
            )
        return self._execute_plan(question, [(scene_id, snapshot)])

    def _ontology_plan(self, question: str) -> MainAgentPlan | None:
        """Route explicit business metrics through the active global Ontology.

        The Ontology can choose several scenes without passing physical schema
        information to the main Agent. SQL execution validation remains authoritative.
        """
        if self.ontology_snapshot_provider is None:
            return None
        snapshot = self.ontology_snapshot_provider()
        if snapshot is None:
            return None
        graph = getattr(snapshot, "graph_json", None)
        if not isinstance(graph, dict):
            return None
        nodes = {
            str(item.get("id")): item
            for item in graph.get("nodes", [])
            if isinstance(item, dict) and item.get("id")
        }
        matched_metrics = []
        normalized = question.lower()
        for node in nodes.values():
            if node.get("type") != "metric":
                continue
            terms = [node.get("id"), node.get("name"), *(node.get("aliases") or [])]
            if any(
                str(term).strip().lower() in normalized
                for term in terms
                if len(str(term).strip()) >= 2
            ):
                matched_metrics.append(node)
        if not matched_metrics:
            return None
        bindings: dict[tuple[str, str], str] = {}
        providers: dict[str, set[str]] = defaultdict(set)
        expected_snapshots = {
            str(item.get("scene_id")): str(item.get("snapshot_id"))
            for item in getattr(snapshot, "source_scene_snapshots", [])
            if isinstance(item, dict)
            and item.get("scene_id")
            and item.get("snapshot_id")
        }
        for edge in graph.get("edges", []):
            if not isinstance(edge, dict):
                continue
            if edge.get("type") == "provided_by":
                providers[str(edge.get("source"))].add(str(edge.get("target")))
            if edge.get("type") == "scene_binding":
                data = edge.get("data") or {}
                key = data.get("semantic_key")
                if key:
                    bindings[(str(edge.get("source")), str(edge.get("target")))] = str(
                        key
                    )
        tasks_by_scene: dict[str, list[str]] = defaultdict(list)
        refs: list[str] = []
        for metric in matched_metrics:
            metric_id = str(metric["id"])
            refs.append(metric_id)
            metric_data = metric.get("data") or {}
            scene_ids = set(providers.get(metric_id, set()))
            if metric_data.get("source_scene"):
                scene_ids.add(str(metric_data["source_scene"]))
            for scene_id in scene_ids:
                try:
                    active_scene = self.registry.get(scene_id)
                except RegistryLookupError:
                    continue
                expected_snapshot_id = expected_snapshots.get(scene_id)
                if (
                    expected_snapshot_id is not None
                    and active_scene.snapshot_id != expected_snapshot_id
                ):
                    # A changed Scene must be synchronized into a new Ontology
                    # Snapshot before the main Agent can rely on its old binding.
                    continue
                key = bindings.get((scene_id, metric_id), metric_id)
                if key not in tasks_by_scene[scene_id]:
                    tasks_by_scene[scene_id].append(key)
        if not tasks_by_scene:
            return None
        if len(tasks_by_scene) > self.max_tasks:
            return MainAgentPlan(
                action="reject",
                reason_code="TOO_MANY_SCENES",
                message=f"At most {self.max_tasks} Scenes can run in one request",
            )
        tasks = [
            PlanTask(
                task_id=f"ontology_{scene_id}",
                scene_id=scene_id,
                question=question,
                metrics=metrics,
                ontology_refs=refs,
            )
            for scene_id, metrics in sorted(tasks_by_scene.items())
        ]
        return MainAgentPlan(
            action="execute",
            ontology_snapshot_id=getattr(snapshot, "snapshot_id", None),
            ontology_refs=refs,
            combine=CombinePlan(mode="separate"),
            tasks=tasks,
        )

    def _execute_plan(
        self, question: str, candidates: list[tuple[str, Snapshot]]
    ) -> MainAgentPlan:
        tasks = []
        for scene_id, snapshot in candidates:
            projection = snapshot.routing_projection
            metrics = snapshot.runtime_config.get("metrics", [])
            if not metrics:
                return MainAgentPlan(
                    action="reject",
                    reason_code="SCENE_HAS_NO_METRICS",
                    message=f"Matched Scene has no queryable metrics: {scene_id}",
                )
            dimension_candidates = projection.get(
                "dimensions", snapshot.runtime_config.get("dimensions", [])
            )
            dimensions = [
                item.get("key")
                for item in dimension_candidates
                if item.get("key") and item.get("key").lower() in question.lower()
            ]
            tasks.append(
                PlanTask(
                    task_id=f"route_{scene_id}",
                    scene_id=scene_id,
                    question=question,
                    metrics=[metrics[0].get("key")],
                    dimensions=dimensions,
                )
            )
        return MainAgentPlan(
            action="execute",
            combine=CombinePlan(mode="separate"),
            tasks=tasks,
        )

    @staticmethod
    def _direct_scene_terms(projection: dict) -> set[str]:
        """Stable names/keywords that unambiguously name a Scene in a question."""
        terms = [projection.get("name", ""), *projection.get("keywords", [])]
        normalized_terms = {
            str(term).strip().lower() for term in terms if len(str(term).strip()) >= 2
        }
        # Chinese Scene names commonly end in generic words such as “分析” or
        # “管理”. Include their leading business prefix so “合同金额” can match
        # a Scene named “合同分析”, without exposing its runtime configuration.
        for term in tuple(normalized_terms):
            for chinese_word in re.findall(r"[\u4e00-\u9fff]{2,}", term):
                normalized_terms.add(chinese_word[:2])
                if len(chinese_word) >= 3:
                    normalized_terms.add(chinese_word[:3])
        return normalized_terms

    @staticmethod
    def _requests_multi_scene(question: str) -> bool:
        """Require an explicit combine/comparison intent before fan-out."""
        return any(
            marker in question
            for marker in (
                "和",
                "与",
                "及",
                "、",
                "以及",
                "同时",
                "分别",
                "对比",
                "比较",
                "compare",
                " and ",
                "versus",
                " vs ",
            )
        )

    @staticmethod
    def _tokens(question: str) -> set[str]:
        return {
            token for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", question) if token
        }

    @staticmethod
    def _contains_injection(question: str) -> bool:
        patterns = (
            r"[;；]",
            r"--|/\*|\*/",
            r"\b(drop|delete|update|insert|alter|truncate|exec|execute)\b",
            r"\b(union\s+select|information_schema|xp_cmdshell)\b",
            r"ignore\s+(all\s+)?previous\s+instructions",
            r"忽略(之前|上面|所有)指令",
            r"system\s+prompt|系统提示词",
        )
        return any(re.search(pattern, question) for pattern in patterns)


__all__ = ["RouterAgent"]
