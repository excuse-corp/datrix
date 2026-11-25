import type {
  OntologyEdge,
  OntologyGraph,
  OntologyNeighborhoodResponse,
  OntologyNode,
  OntologyReviewItem,
  OntologyReviewState,
} from '@/utils/ontology';
import {
  AimOutlined,
  BranchesOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  CloseOutlined,
  CompressOutlined,
  FullscreenOutlined,
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { Button, Checkbox, Descriptions, Empty, Input, Segmented, Select, Space, Tag, Tooltip, message } from 'antd';
import type { CSSProperties } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Background,
  BaseEdge,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  useStore,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  type OnNodesChange,
  type Viewport,
} from 'reactflow';
import styles from './OntologyGraphCanvas.module.css';
import OntologyReviewDetails from './OntologyReviewDetails';

const NODE_WIDTH = 34;
const NODE_HEIGHT = 34;
const ROW_GAP = 72;
const OVERVIEW_NODE_LIMIT = 300;
const OVERVIEW_EDGE_LIMIT = 1000;
const NEIGHBORHOOD_NODE_LIMIT = 120;
const NEIGHBORHOOD_EDGE_LIMIT = 300;
const EMPTY_LAYOUT: Record<string, unknown> = {};

const COLOR: Record<OntologyNode['type'], string> = {
  entity: 'var(--ontology-entity)',
  metric: 'var(--ontology-metric)',
  scene: 'var(--ontology-scene)',
  rule: 'var(--ontology-rule)',
  analysis_dimension: 'var(--ontology-analysis)',
  analysis_path: 'var(--ontology-analysis)',
  analysis_rule: 'var(--ontology-analysis)',
};

const EDGE_COLOR: Record<string, string> = {
  relation: 'var(--ontology-edge)',
  belongs_to: 'var(--ontology-entity)',
  provided_by: 'var(--ontology-metric)',
  scene_binding: 'var(--ontology-scene)',
  cross_scene_join: 'var(--ontology-cross-scene)',
  clarification_rule: 'var(--ontology-rule)',
  metric_relation: 'var(--ontology-analysis)',
};

const TYPE_LABEL: Record<OntologyNode['type'], string> = {
  entity: '实体',
  metric: '指标',
  scene: '场景',
  rule: '澄清规则',
  analysis_dimension: '分析维度',
  analysis_path: '分析路径',
  analysis_rule: '分析规则',
};

const EDGE_LABEL: Record<string, string> = {
  relation: '实体关系',
  belongs_to: '所属',
  provided_by: '场景提供',
  scene_binding: '场景绑定',
  cross_scene_join: '跨场景',
  clarification_rule: '澄清规则',
};

const LAYERS = [
  { value: 'business_semantics', label: '业务语义层' },
  { value: 'analysis_explanations', label: '分析解释层' },
  { value: 'mapping_governance', label: '映射治理层' },
] as const;
const FLAT_LAYOUT_KEY = '__ontology_layout_flat__';
const LAYERED_LAYOUT_KEY = '__ontology_layout_layered__';

function edgeOrigin(edge: OntologyEdge) {
  if (edge.type === 'attribute_reference') return '属性引用';
  if (edge.type === 'rule_reference') return '规则引用';
  return '显式关系';
}

const NODE_SHAPE_CLASS: Record<OntologyNode['type'], string> = {
  entity: styles.entityShape,
  metric: styles.metricShape,
  scene: styles.sceneShape,
  rule: styles.ruleShape,
  analysis_dimension: styles.dimensionShape,
  analysis_path: styles.pathShape,
  analysis_rule: styles.analysisRuleShape,
};

type OntologyGraphCanvasProps = {
  graph?: OntologyGraph;
  graphLayer: 'all' | 'business_semantics' | 'analysis_explanations' | 'mapping_governance';
  onGraphLayerChange: (layer: 'all' | 'business_semantics' | 'analysis_explanations' | 'mapping_governance') => void;
  layout?: Record<string, unknown>;
  onSaveLayout?: (layout: Record<string, unknown>) => Promise<void> | void;
  reviewState?: OntologyReviewState;
  onReviewObject?: (
    item: OntologyReviewItem,
    decision: 'confirmed' | 'rejected' | 'candidate',
    comment: string,
  ) => Promise<void> | void;
  focusTarget?: Pick<OntologyReviewItem, 'target_type' | 'target_id'>;
  onFocusTargetHandled?: () => void;
  graphRevision?: number;
  hasUnsavedGraph?: boolean;
  onLoadNeighborhood?: (nodeId: string) => Promise<OntologyNeighborhoodResponse>;
};

type SelectedElement = { kind: 'node'; value: OntologyNode } | { kind: 'edge'; value: OntologyEdge };

type FlowNodeData = {
  raw: OntologyNode;
  label: string;
  color: string;
  relationCount: number;
  dimmed: boolean;
  searchMatched: boolean;
  hovered: boolean;
  focused: boolean;
  labelVisible: boolean;
  reviewStatus: string;
  reviewRequired: boolean;
  depth: number;
};

type FlowEdgeData = {
  raw: OntologyEdge;
  curveOffset: number;
};

type FlowNode = Node<FlowNodeData>;
type FlowEdge = Edge<FlowEdgeData>;
type GraphLayerPlane = {
  layer: string;
  label: string;
  depth: number;
  x: number;
  y: number;
  width: number;
  height: number;
};

type GraphPayload = {
  nodes: FlowNode[];
  edges: FlowEdge[];
  visibleRawNodes: OntologyNode[];
  visibleRawEdges: OntologyEdge[];
  visibleNodeIds: Set<string>;
  visibleEdgeIds: Set<string>;
  visibleNodeCount: number;
  visibleEdgeCount: number;
  totalNodeCount: number;
  totalEdgeCount: number;
  hasSavedLayout: boolean;
};

function parsePosition(value: unknown): { x: number; y: number } | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const candidate = value as { x?: unknown; y?: unknown };
  const x = Number(candidate.x);
  const y = Number(candidate.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return undefined;
  return { x, y };
}

function textValue(value: unknown, fallback = '') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

function nodeDiameter(relationCount: number) {
  return Math.min(52, 30 + Math.sqrt(relationCount) * 3.2);
}

function stringifyData(value: unknown) {
  if (!value || typeof value !== 'object' || !Object.keys(value as Record<string, unknown>).length) return '无';
  return JSON.stringify(value, null, 2);
}

function nodeSourceLabel(node: OntologyNode) {
  const provenance = node.provenance?.map(item => item.scene_id).filter(Boolean) ?? [];
  const sourceScene = textValue(node.data?.source_scene);
  const sources = Array.from(new Set([...provenance, sourceScene].filter(Boolean)));
  return sources.length ? sources.join('、') : '人工维护';
}

function nodeSearchHaystack(node: OntologyNode) {
  return `${node.id} ${node.name} ${(node.aliases ?? []).join(' ')} ${node.description ?? ''}`.toLowerCase();
}

function sortNodeForLayout(left: OntologyNode, right: OntologyNode) {
  const leftScene = textValue(left.data?.source_scene) || left.provenance?.[0]?.scene_id || '';
  const rightScene = textValue(right.data?.source_scene) || right.provenance?.[0]?.scene_id || '';
  if (leftScene !== rightScene) return leftScene.localeCompare(rightScene);
  return (left.name || left.id).localeCompare(right.name || right.id);
}

function buildSceneNodeIds(graph: OntologyGraph | undefined, scene: string | undefined) {
  const ids = new Set<string>();
  if (!graph || !scene) return ids;
  ids.add(scene);
  graph.edges.forEach(edge => {
    if (edge.source === scene || edge.target === scene || edge.data?.source_scene === scene) {
      ids.add(edge.source);
      ids.add(edge.target);
    }
  });
  graph.nodes.forEach(node => {
    if (node.data?.source_scene === scene) ids.add(node.id);
  });
  return ids;
}

function nodeLayer(type: OntologyNode['type']) {
  if (['scene'].includes(type)) return 'mapping_governance';
  if (['rule', 'analysis_dimension', 'analysis_path', 'analysis_rule'].includes(type)) {
    return 'analysis_explanations';
  }
  return 'business_semantics';
}

function buildDefaultPositions(nodes: OntologyNode[], spatial = false, edges: OntologyEdge[] = []) {
  if (spatial) {
    const neighbors = new Map(nodes.map(node => [node.id, new Set<string>()]));
    edges.forEach(edge => {
      neighbors.get(edge.source)?.add(edge.target);
      neighbors.get(edge.target)?.add(edge.source);
    });
    const groups = LAYERS.map(({ value }) =>
      nodes.filter(node => nodeLayer(node.type) === value).sort(sortNodeForLayout),
    );
    const rank = new Map<string, number>();
    const updateRanks = (group: OntologyNode[]) => {
      group.forEach((node, index) => rank.set(node.id, (index + 0.5) / group.length));
    };
    groups.forEach(updateRanks);
    // Align linked objects within adjacent bands while keeping the layers visible.
    for (let sweep = 0; sweep < 4; sweep += 1) {
      for (const group of sweep % 2 ? [...groups].reverse() : groups) {
        const byNeighborRank = new Map(
          group.map(node => {
            const linked = [...(neighbors.get(node.id) ?? [])].filter(id => rank.has(id));
            const average = linked.length
              ? linked.reduce((total, id) => total + (rank.get(id) ?? 0.5), 0) / linked.length
              : (rank.get(node.id) ?? 0.5);
            return [node.id, average];
          }),
        );
        group.sort(
          (left, right) =>
            (byNeighborRank.get(left.id) ?? 0.5) - (byNeighborRank.get(right.id) ?? 0.5) ||
            sortNodeForLayout(left, right),
        );
        updateRanks(group);
      }
    }
    const positions: Record<string, { x: number; y: number }> = {};
    let x = 0;
    groups.forEach(group => {
      const rows = Math.max(1, Math.ceil(group.length / 4));
      const columns = Math.ceil(group.length / rows);
      group.forEach((node, index) => {
        positions[node.id] = {
          x: x + Math.floor(index / rows) * 114,
          y: (index % rows) * ROW_GAP,
        };
      });
      x += Math.max(1, columns) * 114 + 48;
    });
    return positions;
  }

  // Start from a stable order so rebuilding the same graph yields the same layout.
  const ordered = [...nodes].sort((left, right) => left.id.localeCompare(right.id));
  const positions: Record<string, { x: number; y: number }> = {};
  const neighbors = new Map(ordered.map(node => [node.id, new Set<string>()]));
  edges.forEach(edge => {
    if (!neighbors.has(edge.source) || !neighbors.has(edge.target)) return;
    neighbors.get(edge.source)?.add(edge.target);
    neighbors.get(edge.target)?.add(edge.source);
  });

  const remaining = new Set(ordered.map(node => node.id));
  const components: string[][] = [];
  while (remaining.size) {
    const first = remaining.values().next().value as string;
    const component = [first];
    remaining.delete(first);
    for (let index = 0; index < component.length; index += 1) {
      [...(neighbors.get(component[index]) ?? [])].sort().forEach(id => {
        if (remaining.delete(id)) component.push(id);
      });
    }
    components.push(component);
  }
  components.sort((left, right) => right.length - left.length || left[0].localeCompare(right[0]));

  let offsetX = 0;
  let offsetY = 0;
  let rowHeight = 0;
  components.forEach(component => {
    const points = component.map((id, index) => {
      const angle = index * Math.PI * (3 - Math.sqrt(5));
      const radius = 85 * Math.sqrt(index);
      return { id, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
    });
    const indexById = new Map(points.map((point, index) => [point.id, index]));
    const links = edges.flatMap(edge => {
      const source = indexById.get(edge.source);
      const target = indexById.get(edge.target);
      return source === undefined || target === undefined || source === target ? [] : [{ source, target }];
    });
    for (let iteration = 0; iteration < 120; iteration += 1) {
      const forces = points.map(() => ({ x: 0, y: 0 }));
      points.forEach((point, index) => {
        for (let otherIndex = index + 1; otherIndex < points.length; otherIndex += 1) {
          const other = points[otherIndex];
          const dx = point.x - other.x || 0.01;
          const dy = point.y - other.y || 0.01;
          const distance = Math.max(20, Math.hypot(dx, dy));
          const force = 10500 / (distance * distance);
          const fx = (dx / distance) * force;
          const fy = (dy / distance) * force;
          forces[index].x += fx;
          forces[index].y += fy;
          forces[otherIndex].x -= fx;
          forces[otherIndex].y -= fy;
        }
      });
      links.forEach(({ source, target }) => {
        const dx = points[target].x - points[source].x;
        const dy = points[target].y - points[source].y;
        const distance = Math.max(1, Math.hypot(dx, dy));
        const force = (distance - 160) * 0.012;
        const fx = (dx / distance) * force;
        const fy = (dy / distance) * force;
        forces[source].x += fx;
        forces[source].y += fy;
        forces[target].x -= fx;
        forces[target].y -= fy;
      });
      const step = Math.max(0.15, 1 - iteration / 120);
      points.forEach((point, index) => {
        point.x += Math.max(-12, Math.min(12, forces[index].x - point.x * 0.002)) * step;
        point.y += Math.max(-12, Math.min(12, forces[index].y - point.y * 0.002)) * step;
      });
    }
    const minX = Math.min(...points.map(point => point.x));
    const maxX = Math.max(...points.map(point => point.x));
    const minY = Math.min(...points.map(point => point.y));
    const maxY = Math.max(...points.map(point => point.y));
    const width = maxX - minX + 240;
    const height = maxY - minY + 160;
    if (offsetX > 0 && offsetX + width > 1400) {
      offsetX = 0;
      offsetY += rowHeight;
      rowHeight = 0;
    }
    points.forEach(point => {
      positions[point.id] = { x: point.x - minX + offsetX, y: point.y - minY + offsetY };
    });
    offsetX += width;
    rowHeight = Math.max(rowHeight, height);
  });
  return positions;
}

function buildGraphPayload(
  graph: OntologyGraph | undefined,
  layout: Record<string, unknown>,
  types: OntologyNode['type'][],
  scene: string | undefined,
  spatial: boolean,
  reviewItems: OntologyReviewItem[],
  focusNodeId?: string,
  neighborhoodOnly = false,
  searchQuery = '',
  pinnedNodeIdsInput: string[] = [],
  pinnedEdgeId?: string,
): GraphPayload {
  if (!graph) {
    return {
      nodes: [],
      edges: [],
      visibleRawNodes: [],
      visibleRawEdges: [],
      visibleNodeIds: new Set(),
      visibleEdgeIds: new Set(),
      visibleNodeCount: 0,
      visibleEdgeCount: 0,
      totalNodeCount: 0,
      totalEdgeCount: 0,
      hasSavedLayout: false,
    };
  }

  const sceneNodeIds = buildSceneNodeIds(graph, scene);
  const filteredRawNodes = graph.nodes.filter(
    node => types.includes(node.type) && (!scene || sceneNodeIds.has(node.id)),
  );
  const neighborhoodIds = new Set<string>();
  if (focusNodeId && neighborhoodOnly) {
    neighborhoodIds.add(focusNodeId);
    graph.edges.forEach(edge => {
      if (edge.source === focusNodeId) neighborhoodIds.add(edge.target);
      if (edge.target === focusNodeId) neighborhoodIds.add(edge.source);
    });
  }
  const focusNodes =
    neighborhoodOnly && focusNodeId ? filteredRawNodes.filter(node => neighborhoodIds.has(node.id)) : filteredRawNodes;
  const candidateIds = new Set(focusNodes.map(node => node.id));
  const degreeByNode = new Map<string, number>();
  const pinnedNodeIds = new Set(pinnedNodeIdsInput);
  graph.edges.forEach(edge => {
    if (!candidateIds.has(edge.source) || !candidateIds.has(edge.target)) return;
    degreeByNode.set(edge.source, (degreeByNode.get(edge.source) ?? 0) + 1);
    degreeByNode.set(edge.target, (degreeByNode.get(edge.target) ?? 0) + 1);
  });
  const query = searchQuery.trim().toLowerCase();
  const nodeLimit = neighborhoodOnly ? NEIGHBORHOOD_NODE_LIMIT : OVERVIEW_NODE_LIMIT;
  const visibleRawNodes =
    focusNodes.length <= nodeLimit
      ? focusNodes
      : [...focusNodes]
          .sort((left, right) => {
            const pinnedOrder = Number(pinnedNodeIds.has(right.id)) - Number(pinnedNodeIds.has(left.id));
            const focusOrder = Number(right.id === focusNodeId) - Number(left.id === focusNodeId);
            const searchOrder =
              Number(nodeSearchHaystack(right).includes(query)) - Number(nodeSearchHaystack(left).includes(query));
            return (
              pinnedOrder ||
              focusOrder ||
              searchOrder ||
              (degreeByNode.get(right.id) ?? 0) - (degreeByNode.get(left.id) ?? 0) ||
              sortNodeForLayout(left, right)
            );
          })
          .slice(0, nodeLimit);
  const visibleNodeIds = new Set(visibleRawNodes.map(node => node.id));
  const availableEdges = graph.edges.filter(
    edge =>
      visibleNodeIds.has(edge.source) &&
      visibleNodeIds.has(edge.target) &&
      (!neighborhoodOnly || !focusNodeId || edge.source === focusNodeId || edge.target === focusNodeId),
  );
  const edgeLimit = neighborhoodOnly ? NEIGHBORHOOD_EDGE_LIMIT : OVERVIEW_EDGE_LIMIT;
  const visibleRawEdges =
    availableEdges.length <= edgeLimit
      ? availableEdges
      : [...availableEdges]
          .sort((left, right) => {
            const pinnedOrder = Number(right.id === pinnedEdgeId) - Number(left.id === pinnedEdgeId);
            const focusOrder =
              Number(right.source === focusNodeId || right.target === focusNodeId) -
              Number(left.source === focusNodeId || left.target === focusNodeId);
            const rightDegree = (degreeByNode.get(right.source) ?? 0) + (degreeByNode.get(right.target) ?? 0);
            const leftDegree = (degreeByNode.get(left.source) ?? 0) + (degreeByNode.get(left.target) ?? 0);
            return pinnedOrder || focusOrder || rightDegree - leftDegree || left.id.localeCompare(right.id);
          })
          .slice(0, edgeLimit);
  const visibleEdgeIds = new Set(visibleRawEdges.map(edge => edge.id));
  const defaultPositions = buildDefaultPositions(visibleRawNodes, spatial, visibleRawEdges);
  const reviewByTarget = new Map(reviewItems.map(item => [`${item.target_type}:${item.target_id}`, item]));

  let savedPositionCount = 0;
  const nodes = visibleRawNodes.map(node => {
    const savedPosition = parsePosition(layout[node.id]);
    if (savedPosition) savedPositionCount += 1;
    return {
      id: node.id,
      type: 'ontologyNode',
      position: savedPosition ?? defaultPositions[node.id] ?? { x: 0, y: 0 },
      data: {
        raw: node,
        label: node.name || node.id,
        color: COLOR[node.type] ?? '#64748b',
        relationCount: degreeByNode.get(node.id) ?? 0,
        dimmed: false,
        searchMatched: false,
        hovered: false,
        focused: false,
        labelVisible: visibleRawNodes.length <= 60,
        reviewStatus: reviewByTarget.get(`node:${node.id}`)?.status ?? 'candidate',
        reviewRequired: reviewByTarget.get(`node:${node.id}`)?.required ?? false,
        depth: ['business_semantics', 'analysis_explanations', 'mapping_governance'].indexOf(nodeLayer(node.type)),
      },
    } satisfies FlowNode;
  });

  const edgeGroups = new Map<string, OntologyEdge[]>();
  visibleRawEdges.forEach(edge => {
    const key = JSON.stringify([edge.source, edge.target].sort());
    edgeGroups.set(key, [...(edgeGroups.get(key) ?? []), edge]);
  });
  const edges = visibleRawEdges.map(edge => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: 'ontologyEdge',
    label: edge.name || EDGE_LABEL[edge.type] || edge.type,
    data: {
      raw: edge,
      curveOffset: (() => {
        const group = edgeGroups.get(JSON.stringify([edge.source, edge.target].sort())) ?? [];
        const ordered = [...group].sort((left, right) => left.id.localeCompare(right.id));
        const offset = (ordered.findIndex(item => item.id === edge.id) - (ordered.length - 1) / 2) * 42;
        return edge.source <= edge.target ? offset : -offset;
      })(),
    },
    markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLOR[edge.type] ?? '#94a3b8' },
    style: { stroke: EDGE_COLOR[edge.type] ?? '#94a3b8', strokeWidth: 1.5 },
    labelStyle: { fill: 'var(--ontology-edge-label)', fontSize: 11, fontWeight: 500 },
    labelBgStyle: { fill: 'var(--ontology-surface)', fillOpacity: 0.94 },
    labelBgPadding: [4, 3] as [number, number],
    labelBgBorderRadius: 4,
  })) satisfies FlowEdge[];

  return {
    nodes,
    edges,
    visibleRawNodes,
    visibleRawEdges,
    visibleNodeIds,
    visibleEdgeIds,
    visibleNodeCount: visibleRawNodes.length,
    visibleEdgeCount: visibleRawEdges.length,
    totalNodeCount: graph.nodes.length,
    totalEdgeCount: graph.edges.length,
    hasSavedLayout: Boolean(visibleRawNodes.length && savedPositionCount > 0),
  };
}

function buildFocusSets(focus: SelectedElement | undefined, edges: FlowEdge[]) {
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  if (focus?.kind === 'node') {
    const id = focus.value.id;
    nodeIds.add(id);
    edges.forEach(edge => {
      if (edge.source !== id && edge.target !== id) return;
      edgeIds.add(edge.id);
      nodeIds.add(edge.source);
      nodeIds.add(edge.target);
    });
  } else if (focus?.kind === 'edge') {
    edgeIds.add(focus.value.id);
    nodeIds.add(focus.value.source);
    nodeIds.add(focus.value.target);
  }
  return { nodeIds, edgeIds };
}

function OntologyNodeCard({ data }: NodeProps<FlowNodeData>) {
  const borderColor = data.searchMatched
    ? 'var(--ontology-warning)'
    : data.focused
      ? 'var(--ontology-focus)'
      : data.color;
  const diameter = nodeDiameter(data.relationCount);
  const reviewClass =
    data.reviewStatus === 'confirmed'
      ? styles.reviewConfirmed
      : data.reviewStatus === 'rejected'
        ? styles.reviewRejected
        : data.reviewRequired
          ? styles.reviewPending
          : '';

  return (
    <div
      aria-label={data.label}
      className={`${styles.graphNode} ${data.labelVisible ? styles.labelsVisible : ''} ${data.hovered ? styles.nodeHovered : ''} ${data.focused ? styles.focusNode : ''} ${
        data.searchMatched ? styles.searchNode : ''
      } ${data.relationCount >= 14 ? styles.highDegree : ''}`}
      style={
        {
          opacity: data.dimmed ? 0.42 : 1,
          '--node-accent': data.color,
          '--node-outline': borderColor,
          '--node-diameter': `${diameter}px`,
        } as CSSProperties
      }
    >
      <span className={styles.nodeHitArea} />
      <Handle type='target' position={Position.Left} isConnectable={false} className='!h-2 !w-2 !border-0 !opacity-0' />
      <Handle
        type='source'
        position={Position.Right}
        isConnectable={false}
        className='!h-2 !w-2 !border-0 !opacity-0'
      />
      <span className={`${styles.graphNodeShape} ${NODE_SHAPE_CLASS[data.raw.type]} ${reviewClass}`} />
      <span className={styles.graphNodeLabel}>{data.label}</span>
    </div>
  );
}

function OntologyRelationEdge(props: EdgeProps<FlowEdgeData>) {
  const source = useStore(state => state.nodeInternals.get(props.source));
  const target = useStore(state => state.nodeInternals.get(props.target));
  if (!source?.positionAbsolute || !target?.positionAbsolute || !source.width || !target.width) return null;

  const sourceCenter = {
    x: source.positionAbsolute.x + source.width / 2,
    y: source.positionAbsolute.y + (source.height ?? source.width) / 2,
  };
  const targetCenter = {
    x: target.positionAbsolute.x + target.width / 2,
    y: target.positionAbsolute.y + (target.height ?? target.width) / 2,
  };
  let path: string;
  let labelX: number;
  let labelY: number;
  if (props.source === props.target) {
    const radius = source.width / 2;
    const loop = 54 + Math.abs(props.data?.curveOffset ?? 0);
    path = `M ${sourceCenter.x + radius * 0.6} ${sourceCenter.y - radius * 0.8} C ${sourceCenter.x + loop} ${sourceCenter.y - loop * 1.6}, ${sourceCenter.x - loop} ${sourceCenter.y - loop * 1.6}, ${sourceCenter.x - radius * 0.6} ${sourceCenter.y - radius * 0.8}`;
    labelX = sourceCenter.x;
    labelY = sourceCenter.y - loop * 1.15;
  } else {
    const dx = targetCenter.x - sourceCenter.x;
    const dy = targetCenter.y - sourceCenter.y;
    const distance = Math.max(1, Math.hypot(dx, dy));
    const offset = props.data?.curveOffset ?? 0;
    const control = {
      x: (sourceCenter.x + targetCenter.x) / 2 - (dy / distance) * offset,
      y: (sourceCenter.y + targetCenter.y) / 2 + (dx / distance) * offset,
    };
    const boundary = (center: { x: number; y: number }, radius: number) => {
      const vx = control.x - center.x;
      const vy = control.y - center.y;
      const length = Math.max(1, Math.hypot(vx, vy));
      return { x: center.x + (vx / length) * radius, y: center.y + (vy / length) * radius };
    };
    const start = boundary(sourceCenter, source.width / 2);
    const end = boundary(targetCenter, target.width / 2 + 3);
    path = `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`;
    labelX = (start.x + 2 * control.x + end.x) / 4;
    labelY = (start.y + 2 * control.y + end.y) / 4;
  }
  return (
    <BaseEdge
      id={props.id}
      path={path}
      markerEnd={props.markerEnd}
      style={props.style}
      label={props.label}
      labelX={labelX}
      labelY={labelY}
      labelStyle={props.labelStyle}
      labelBgStyle={props.labelBgStyle}
      labelBgPadding={props.labelBgPadding}
      labelBgBorderRadius={props.labelBgBorderRadius}
      interactionWidth={props.interactionWidth}
    />
  );
}

const nodeTypes = { ontologyNode: OntologyNodeCard };
const edgeTypes = { ontologyEdge: OntologyRelationEdge };

function OntologyGraphInner({
  graph,
  graphLayer,
  onGraphLayerChange,
  layout = EMPTY_LAYOUT,
  onSaveLayout,
  reviewState,
  onReviewObject,
  focusTarget,
  onFocusTargetHandled,
  graphRevision,
  hasUnsavedGraph = false,
  onLoadNeighborhood,
}: OntologyGraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const depthPlanesRef = useRef<HTMLDivElement>(null);
  const reactFlow = useReactFlow<FlowNodeData, FlowEdgeData>();
  const [nodes, setNodes, onNodesChangeBase] = useNodesState<FlowNodeData>([]);
  const [edges, setEdges] = useEdgesState<FlowEdgeData>([]);
  const [types, setTypes] = useState<OntologyNode['type'][]>([
    'entity',
    'metric',
    'scene',
    'rule',
    'analysis_dimension',
    'analysis_path',
    'analysis_rule',
  ]);
  const [scene, setScene] = useState<string>();
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<SelectedElement>();
  const [neighborhoodResponse, setNeighborhoodResponse] = useState<OntologyNeighborhoodResponse>();
  const [neighborhoodLoading, setNeighborhoodLoading] = useState(false);
  const neighborhoodRequestId = useRef(0);
  const [focusPinIds, setFocusPinIds] = useState<string[]>([]);
  const [focusPinEdgeId, setFocusPinEdgeId] = useState<string>();
  const [fullscreen, setFullscreen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [layoutDirty, setLayoutDirty] = useState(false);
  const [spatial, setSpatial] = useState(true);
  const [showRuleCandidates, setShowRuleCandidates] = useState(true);
  const [hovered, setHovered] = useState<SelectedElement>();
  const [neighborhoodOnly, setNeighborhoodOnly] = useState(false);
  const [neighborhoodCenter, setNeighborhoodCenter] = useState<string>();
  const overviewViewport = useRef<Viewport>();
  const workingPositions = useRef<Record<string, Record<string, { x: number; y: number }>>>({});
  const layoutKey = spatial ? LAYERED_LAYOUT_KEY : FLAT_LAYOUT_KEY;
  const restoreOverview = useRef(false);
  const [searchTarget, setSearchTarget] = useState<string>();
  const [reviewComment, setReviewComment] = useState('');

  useEffect(() => {
    neighborhoodRequestId.current += 1;
    setNeighborhoodResponse(undefined);
    setNeighborhoodLoading(false);
    setNeighborhoodOnly(false);
    setNeighborhoodCenter(undefined);
    setSelected(undefined);
    setFocusPinIds([]);
    setFocusPinEdgeId(undefined);
    workingPositions.current = {};
    setLayoutDirty(false);
  }, [graphRevision]);

  useEffect(() => {
    if (!hasUnsavedGraph) return;
    neighborhoodRequestId.current += 1;
    setNeighborhoodResponse(undefined);
    setNeighborhoodLoading(false);
    setNeighborhoodOnly(false);
  }, [hasUnsavedGraph]);

  const sceneOptions = useMemo(
    () =>
      (graph?.nodes ?? []).filter(node => node.type === 'scene').map(node => ({ value: node.id, label: node.name })),
    [graph],
  );

  const reviewItems = useMemo(() => reviewState?.items ?? [], [reviewState?.items]);
  const neighborhoodFocusNodeId = neighborhoodOnly ? neighborhoodCenter : undefined;
  const viewGraph = useMemo(() => {
    if (!graph) return undefined;
    if (!neighborhoodResponse || neighborhoodResponse.center_node_id !== neighborhoodFocusNodeId) {
      return graph;
    }
    const allowedNodeIds = new Set((graph?.nodes ?? []).map(node => node.id));
    const nodes = neighborhoodResponse.graph.nodes.filter(node => allowedNodeIds.has(node.id));
    const includedIds = new Set(nodes.map(node => node.id));
    const returnedEdgeIds = new Set(neighborhoodResponse.graph.edges.map(edge => edge.id));
    const edges = graph.edges.filter(
      edge => returnedEdgeIds.has(edge.id) && includedIds.has(edge.source) && includedIds.has(edge.target),
    );
    return { nodes, edges };
  }, [graph, neighborhoodFocusNodeId, neighborhoodResponse]);
  const pinnedNodeIds = useMemo(() => {
    const ids = new Set<string>();
    focusPinIds.forEach(id => ids.add(id));
    if (focusTarget?.target_type === 'node') ids.add(focusTarget.target_id);
    if (focusTarget?.target_type === 'edge') {
      const edge = (viewGraph?.edges ?? []).find(item => item.id === focusTarget.target_id);
      if (edge) {
        ids.add(edge.source);
        ids.add(edge.target);
      }
    }
    return [...ids];
  }, [focusPinIds, focusTarget, viewGraph]);
  const pinnedEdgeId = focusTarget?.target_type === 'edge' ? focusTarget.target_id : focusPinEdgeId;
  const graphPayload = useMemo(() => {
    const payload = buildGraphPayload(
      viewGraph && {
        nodes: viewGraph.nodes,
        edges: viewGraph.edges.filter(
          edge =>
            edge.type !== 'rule_reference' ||
            edge.data?.review_status === 'confirmed' ||
            showRuleCandidates ||
            (focusTarget?.target_type === 'edge' && edge.id === focusTarget.target_id),
        ),
      },
      {
        ...(!spatial && !(FLAT_LAYOUT_KEY in layout) ? layout : {}),
        ...(layout[layoutKey] as Record<string, unknown> | undefined),
        ...workingPositions.current[layoutKey],
      },
      types,
      scene,
      spatial,
      reviewItems,
      neighborhoodFocusNodeId,
      neighborhoodOnly,
      '',
      pinnedNodeIds,
      pinnedEdgeId,
    );
    return neighborhoodResponse
      ? {
          ...payload,
          totalNodeCount: neighborhoodResponse.total_node_count,
          totalEdgeCount: neighborhoodResponse.total_edge_count,
        }
      : { ...payload, totalNodeCount: graph?.nodes.length ?? 0, totalEdgeCount: graph?.edges.length ?? 0 };
  }, [
    focusTarget,
    graph,
    layout,
    layoutKey,
    neighborhoodFocusNodeId,
    neighborhoodOnly,
    neighborhoodResponse,
    pinnedEdgeId,
    pinnedNodeIds,
    reviewItems,
    scene,
    showRuleCandidates,
    spatial,
    types,
    viewGraph,
  ]);
  const layerPlanes = useMemo<GraphLayerPlane[]>(
    () =>
      spatial
        ? LAYERS.flatMap(({ value: layer, label }, depth) => {
            const members = nodes.filter(node => node.data.depth === depth);
            if (!members.length) return [];
            const left = Math.min(...members.map(node => node.position.x));
            const right = Math.max(...members.map(node => node.position.x + NODE_WIDTH));
            const top = Math.min(...members.map(node => node.position.y));
            const bottom = Math.max(...members.map(node => node.position.y + NODE_HEIGHT));
            return [
              {
                layer,
                label,
                depth,
                x: left - 42,
                y: top - 80,
                width: right - left + 84,
                height: bottom - top + 142,
              },
            ];
          })
        : [],
    [nodes, spatial],
  );

  const searchResults = useMemo(() => {
    const query = search.trim().toLowerCase();
    return query ? (graph?.nodes ?? []).filter(node => nodeSearchHaystack(node).includes(query)) : [];
  }, [graph, search]);

  const searchMatchCount = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return 0;
    return graphPayload.visibleRawNodes.filter(node => nodeSearchHaystack(node).includes(query)).length;
  }, [graphPayload.visibleRawNodes, search]);

  const layerFocus = useMemo(() => {
    const nodeIds = new Set<string>();
    const edgeIds = new Set<string>();
    if (graphLayer === 'all') return { nodeIds, edgeIds };
    const layerNodes = new Set(nodes.filter(node => nodeLayer(node.data.raw.type) === graphLayer).map(node => node.id));
    layerNodes.forEach(id => nodeIds.add(id));
    edges.forEach(edge => {
      if (!layerNodes.has(edge.source) && !layerNodes.has(edge.target)) return;
      edgeIds.add(edge.id);
      nodeIds.add(edge.source);
      nodeIds.add(edge.target);
    });
    return { nodeIds, edgeIds };
  }, [graphLayer, nodes, edges]);

  const focus = hovered ?? selected;
  const focusSets = useMemo(() => buildFocusSets(focus, edges), [edges, focus]);

  const renderedNodes = useMemo(() => {
    const query = search.trim().toLowerCase();
    return nodes.map(node => {
      const searchMatched = Boolean(query && nodeSearchHaystack(node.data.raw).includes(query));
      const highlighted = focusSets.nodeIds.has(node.id);
      const focused =
        (hovered?.kind === 'node' && hovered.value.id === node.id) ||
        (selected?.kind === 'node' && selected.value.id === node.id) ||
        (selected?.kind === 'edge' && [selected.value.source, selected.value.target].includes(node.id));
      return {
        ...node,
        style: {
          width: nodeDiameter(node.data.relationCount),
          height: nodeDiameter(node.data.relationCount),
        },
        data: {
          ...node.data,
          dimmed: Boolean(
            (focus ? !highlighted : graphLayer !== 'all' && !layerFocus.nodeIds.has(node.id)) ||
              (query && searchMatchCount > 0 && !searchMatched),
          ),
          searchMatched,
          hovered: hovered?.kind === 'node' && hovered.value.id === node.id,
          labelVisible: node.data.labelVisible || highlighted || node.data.raw.type === 'scene',
          focused,
        },
      };
    });
  }, [focus, focusSets.nodeIds, graphLayer, hovered, layerFocus.nodeIds, nodes, search, searchMatchCount, selected]);

  const renderedEdges = useMemo(() => {
    return edges.map(edge => {
      const raw = edge.data?.raw;
      const baseColor = raw ? (EDGE_COLOR[raw.type] ?? 'var(--ontology-edge)') : 'var(--ontology-edge)';
      const reviewRequired = Boolean(raw?.data?.review_required);
      const reviewStatus = String(raw?.data?.review_status ?? 'candidate');
      const ruleReference = raw?.type === 'rule_reference';
      const attributeReference = raw?.type === 'attribute_reference';
      const candidate = ruleReference && reviewStatus !== 'confirmed';
      const color =
        reviewStatus === 'rejected'
          ? 'var(--ontology-review-rejected)'
          : candidate || (reviewRequired && reviewStatus !== 'confirmed')
            ? 'var(--ontology-review-pending)'
            : attributeReference || ruleReference
              ? 'var(--ontology-reference)'
              : baseColor;
      const focused = Boolean(focus);
      const highlighted = focused
        ? focusSets.edgeIds.has(edge.id)
        : graphLayer === 'all' || layerFocus.edgeIds.has(edge.id);
      return {
        ...edge,
        type: 'ontologyEdge',
        animated: false,
        markerEnd: { type: MarkerType.ArrowClosed, color },
        style: {
          stroke: color,
          strokeWidth: focused && highlighted ? 2.7 : 1.6,
          opacity: highlighted ? 0.94 : 0.21,
          strokeDasharray: candidate ? '2 5' : attributeReference || ruleReference ? '7 4' : undefined,
        },
        label: highlighted && (focused || neighborhoodOnly) ? edge.label : undefined,
        labelStyle: {
          fill: 'var(--ontology-edge-label)',
          fontSize: 11,
          fontWeight: focused ? 700 : 500,
        },
      } satisfies FlowEdge;
    });
  }, [edges, focus, focusSets.edgeIds, graphLayer, layerFocus.edgeIds, neighborhoodOnly]);

  const updateDepthPlanes = useCallback((nextViewport: Viewport) => {
    const container = containerRef.current;
    if (container) {
      container.dataset.zoomTier =
        nextViewport.zoom < 0.45 ? 'overview' : nextViewport.zoom < 1.55 ? 'labels' : 'detail';
    }
    const planes = depthPlanesRef.current;
    if (!planes) return;
    planes.style.setProperty('--viewport-x', `${nextViewport.x}px`);
    planes.style.setProperty('--viewport-y', `${nextViewport.y}px`);
    planes.style.setProperty('--viewport-zoom', String(nextViewport.zoom));
  }, []);

  const onMove = useCallback(
    (_event: MouseEvent | TouchEvent | null, nextViewport: Viewport) => {
      updateDepthPlanes(nextViewport);
    },
    [updateDepthPlanes],
  );

  useEffect(() => {
    setNodes(graphPayload.nodes);
    setEdges(graphPayload.edges);
    setSelected(current => {
      if (!current) return current;
      if (current.kind === 'node') {
        const node = graphPayload.visibleRawNodes.find(item => item.id === current.value.id);
        return node ? { kind: 'node', value: node } : undefined;
      }
      const edge = graphPayload.visibleRawEdges.find(item => item.id === current.value.id);
      return edge ? { kind: 'edge', value: edge } : undefined;
    });
    window.requestAnimationFrame(() => {
      if (restoreOverview.current && overviewViewport.current) {
        restoreOverview.current = false;
        void reactFlow.setViewport(overviewViewport.current, { duration: 180 });
      } else {
        void reactFlow.fitView({ padding: 0.18, maxZoom: 1.2, duration: 180 });
      }
      window.requestAnimationFrame(() => updateDepthPlanes(reactFlow.getViewport()));
    });
  }, [graphPayload, reactFlow, setEdges, setNodes, updateDepthPlanes]);

  useEffect(() => {
    if (!selected) return;
    const visible = selected.kind === 'node' ? graphPayload.visibleNodeIds : graphPayload.visibleEdgeIds;
    const id = selected.kind === 'node' ? selected.value.id : selected.value.id;
    if (!visible.has(id)) setSelected(undefined);
  }, [graphPayload.visibleEdgeIds, graphPayload.visibleNodeIds, selected]);

  useEffect(() => {
    setReviewComment('');
  }, [selected?.kind, selected?.value.id]);

  useEffect(() => {
    if (!focusTarget) return;
    if (neighborhoodOnly) {
      neighborhoodRequestId.current += 1;
      setNeighborhoodResponse(undefined);
      setNeighborhoodLoading(false);
      setNeighborhoodOnly(false);
      setNeighborhoodCenter(undefined);
      return;
    }
    if (scene || types.length !== 7) {
      setTypes(['entity', 'metric', 'scene', 'rule', 'analysis_dimension', 'analysis_path', 'analysis_rule']);
      setScene(undefined);
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      const flowNodes = graphPayload.nodes;
      let found = false;
      if (focusTarget.target_type === 'node') {
        const target = flowNodes.find(node => node.id === focusTarget.target_id);
        if (target) {
          found = true;
          setFocusPinIds([target.id]);
          setFocusPinEdgeId(undefined);
          setSelected({ kind: 'node', value: target.data.raw });
          reactFlow.setCenter(target.position.x + NODE_WIDTH / 2, target.position.y + NODE_HEIGHT / 2, {
            zoom: Math.max(reactFlow.getZoom(), 1.05),
            duration: 220,
          });
        }
      } else {
        const target = graphPayload.visibleRawEdges.find(edge => edge.id === focusTarget.target_id);
        if (target) {
          found = true;
          setFocusPinIds([target.source, target.target]);
          setFocusPinEdgeId(target.id);
          if (target.type === 'rule_reference') setShowRuleCandidates(true);
          setSelected({ kind: 'edge', value: target });
          const source = flowNodes.find(node => node.id === target.source);
          const destination = flowNodes.find(node => node.id === target.target);
          if (source && destination) {
            reactFlow.setCenter(
              (source.position.x + destination.position.x) / 2 + NODE_WIDTH / 2,
              (source.position.y + destination.position.y) / 2 + NODE_HEIGHT / 2,
              { zoom: Math.max(reactFlow.getZoom(), 0.9), duration: 220 },
            );
          }
        }
      }
      if (found) onFocusTargetHandled?.();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [
    focusTarget,
    neighborhoodOnly,
    graphPayload.nodes,
    graphPayload.visibleRawEdges,
    onFocusTargetHandled,
    reactFlow,
    scene,
    types.length,
  ]);

  useEffect(() => {
    const onFullscreenChange = () => {
      setFullscreen(Boolean(document.fullscreenElement));
      window.requestAnimationFrame(() => {
        reactFlow.fitView({ padding: 0.18, maxZoom: 1.2, duration: 180 });
        window.requestAnimationFrame(() => updateDepthPlanes(reactFlow.getViewport()));
      });
    };
    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, [reactFlow, updateDepthPlanes]);

  const onNodesChange: OnNodesChange = useCallback(
    changes => {
      onNodesChangeBase(changes);
    },
    [onNodesChangeBase],
  );

  const fitView = useCallback(() => {
    reactFlow.fitView({ padding: 0.18, maxZoom: 1.2, duration: 220 });
  }, [reactFlow]);

  const loadNeighborhood = useCallback(
    async (node: OntologyNode) => {
      if (!neighborhoodOnly) overviewViewport.current = reactFlow.getViewport();
      neighborhoodRequestId.current += 1;
      setNeighborhoodResponse(undefined);
      setNeighborhoodLoading(false);
      setNeighborhoodCenter(node.id);
      setSelected({ kind: 'node', value: node });
      setNeighborhoodOnly(true);
      if (!onLoadNeighborhood || hasUnsavedGraph) {
        setNeighborhoodResponse(undefined);
        return;
      }

      const requestId = neighborhoodRequestId.current + 1;
      neighborhoodRequestId.current = requestId;
      setNeighborhoodLoading(true);
      try {
        const response = await onLoadNeighborhood(node.id);
        if (neighborhoodRequestId.current === requestId) setNeighborhoodResponse(response);
      } catch (error) {
        if (neighborhoodRequestId.current === requestId) {
          setNeighborhoodResponse(undefined);
          message.error(error instanceof Error ? error.message : '加载节点邻域失败');
        }
      } finally {
        if (neighborhoodRequestId.current === requestId) setNeighborhoodLoading(false);
      }
    },
    [hasUnsavedGraph, neighborhoodOnly, onLoadNeighborhood, reactFlow],
  );

  const showFullGraph = useCallback(() => {
    neighborhoodRequestId.current += 1;
    setNeighborhoodResponse(undefined);
    setNeighborhoodLoading(false);
    setNeighborhoodOnly(false);
    setNeighborhoodCenter(undefined);
    restoreOverview.current = true;
  }, []);

  const toggleNeighborhood = useCallback(() => {
    if (neighborhoodOnly) {
      showFullGraph();
      return;
    }
    if (selected?.kind === 'node') void loadNeighborhood(selected.value);
  }, [loadNeighborhood, neighborhoodOnly, selected, showFullGraph]);

  const runAutoLayout = useCallback(() => {
    const positions = buildDefaultPositions(graphPayload.visibleRawNodes, spatial, graphPayload.visibleRawEdges);
    workingPositions.current[layoutKey] = { ...workingPositions.current[layoutKey], ...positions };
    setNodes(current =>
      current.map(node => ({
        ...node,
        position: positions[node.id] ?? node.position,
      })),
    );
    setLayoutDirty(true);
    window.requestAnimationFrame(fitView);
  }, [fitView, graphPayload.visibleRawEdges, graphPayload.visibleRawNodes, layoutKey, setNodes, spatial]);

  const saveLayout = async () => {
    if (!onSaveLayout) return;
    setSaving(true);
    try {
      const saved: Record<string, unknown> = { ...layout };
      const activePositions = {
        ...(!spatial && !(FLAT_LAYOUT_KEY in layout) ? layout : {}),
        ...(layout[layoutKey] as Record<string, unknown> | undefined),
        ...workingPositions.current[layoutKey],
      };
      reactFlow.getNodes().forEach(node => {
        activePositions[node.id] = node.position;
      });
      Object.assign(saved, workingPositions.current, { [layoutKey]: activePositions });
      await onSaveLayout(saved);
      workingPositions.current = {};
      setLayoutDirty(false);
    } finally {
      setSaving(false);
    }
  };

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await containerRef.current?.requestFullscreen();
    } finally {
      setFullscreen(Boolean(document.fullscreenElement));
    }
  };

  const locateSearchResult = (node: OntologyNode) => {
    showFullGraph();
    restoreOverview.current = false;
    onGraphLayerChange('all');
    setScene(undefined);
    setTypes(Object.keys(TYPE_LABEL) as OntologyNode['type'][]);
    setFocusPinIds([node.id]);
    setFocusPinEdgeId(undefined);
    setSearchTarget(node.id);
    setSearch('');
  };

  useEffect(() => {
    if (!searchTarget) return;
    const target = graphPayload.nodes.find(node => node.id === searchTarget);
    if (!target) return;
    const frame = window.requestAnimationFrame(() => {
      setSelected({ kind: 'node', value: target.data.raw });
      void reactFlow.setCenter(target.position.x + NODE_WIDTH / 2, target.position.y + NODE_HEIGHT / 2, {
        zoom: Math.max(reactFlow.getZoom(), 1.05),
        duration: 220,
      });
      setSearchTarget(undefined);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [graphPayload.nodes, reactFlow, searchTarget]);

  const locateFirstSearchMatch = () => {
    if (searchResults[0]) locateSearchResult(searchResults[0]);
  };

  const selectedType = selected?.kind === 'node' ? selected.value.type : selected?.value.type;
  const selectedTagColor =
    selected?.kind === 'node'
      ? COLOR[selected.value.type]
      : selected?.kind === 'edge'
        ? (EDGE_COLOR[selected.value.type] ?? '#64748b')
        : undefined;
  const selectedTagLabel =
    selected?.kind === 'node'
      ? TYPE_LABEL[selected.value.type]
      : selected?.kind === 'edge'
        ? EDGE_LABEL[selected.value.type] || selected.value.type
        : '';
  const empty = !graphPayload.visibleNodeCount;
  const selectedReview = selected
    ? reviewItems.find(
        item =>
          item.target_type === selected.kind &&
          item.target_id === (selected.kind === 'node' ? selected.value.id : selected.value.id),
      )
    : undefined;
  const selectedReviewEvents = selected
    ? (reviewState?.events ?? [])
        .filter(
          event =>
            event.event_type === 'object_review' &&
            event.target_type === selected.kind &&
            event.target_id === selected.value.id,
        )
        .slice(-5)
        .reverse()
    : [];

  const allTypes = Object.keys(TYPE_LABEL) as OntologyNode['type'][];
  const layerOptions = [{ value: 'all', label: '全部层' } as const, ...LAYERS];
  const selectedRelations =
    selected?.kind === 'node'
      ? graphPayload.visibleRawEdges.filter(
          edge => edge.source === selected.value.id || edge.target === selected.value.id,
        )
      : [];
  const nodeById = new Map((graph?.nodes ?? []).map(node => [node.id, node]));

  return (
    <div ref={containerRef} className={styles.workbench}>
      <div className={styles.workbenchToolbar}>
        <Segmented
          value={neighborhoodOnly ? 'neighborhood' : 'overview'}
          onChange={value => {
            if (value === 'overview') showFullGraph();
            else if (selected?.kind === 'node') void loadNeighborhood(selected.value);
          }}
          options={[
            { value: 'overview', label: '图谱概览' },
            { value: 'neighborhood', label: '一跳邻域', disabled: selected?.kind !== 'node' && !neighborhoodOnly },
          ]}
        />
        <Select
          showSearch
          allowClear
          aria-label='搜索本体节点'
          className={styles.search}
          placeholder='搜索节点名称、别名或标识'
          searchValue={search}
          value={undefined}
          filterOption={false}
          onSearch={setSearch}
          onClear={() => setSearch('')}
          onSelect={value => {
            const node = graph?.nodes.find(item => item.id === value);
            if (node) locateSearchResult(node);
          }}
          options={searchResults.map(node => ({
            value: node.id,
            label: `${node.name || node.id} · ${TYPE_LABEL[node.type]}`,
          }))}
          notFoundContent={search ? '没有匹配的节点' : '输入名称、别名或标识'}
        />
      </div>
      <div className={styles.workbenchBody}>
        <aside className={styles.filters} aria-label='筛选图谱'>
          <div className={styles.filterHeader}>
            <strong>筛选</strong>
            <Button
              type='link'
              size='small'
              onClick={() => {
                setTypes(allTypes);
                setScene(undefined);
                showFullGraph();
                restoreOverview.current = false;
                onGraphLayerChange('all');
              }}
            >
              重置
            </Button>
          </div>
          <div className={styles.filterTitle}>本体层</div>
          {layerOptions.map(option => (
            <button
              key={option.value}
              type='button'
              className={styles.layerButton}
              aria-pressed={graphLayer === option.value}
              onClick={() => {
                setSelected(undefined);
                setHovered(undefined);
                onGraphLayerChange(option.value);
              }}
            >
              <span className={styles.layerName}>
                {option.value !== 'all' && <i className={styles.layerDot} data-layer={option.value} />}
                {option.label}
              </span>
              <span>
                {option.value === 'all'
                  ? (graph?.nodes.length ?? 0)
                  : (graph?.nodes ?? []).filter(node => nodeLayer(node.type) === option.value).length}
              </span>
            </button>
          ))}
          <div className={styles.filterDivider} />
          <div className={styles.filterTitle}>节点类型</div>
          {allTypes.map(type => (
            <label key={type} className={styles.typeFilter}>
              <Checkbox
                checked={types.includes(type)}
                onChange={event =>
                  setTypes(current =>
                    event.target.checked ? [...current, type] : current.filter(item => item !== type),
                  )
                }
              />
              <span className={`${styles.typeSwatch} ${NODE_SHAPE_CLASS[type]}`} style={{ background: COLOR[type] }} />
              <span>{TYPE_LABEL[type]}</span>
              <span className={styles.filterCount}>
                {(graph?.nodes ?? []).filter(node => node.type === type).length}
              </span>
            </label>
          ))}
          <div className={styles.filterDivider} />
          <Checkbox checked={showRuleCandidates} onChange={event => setShowRuleCandidates(event.target.checked)}>
            规则候选连线
          </Checkbox>
          <div className={styles.filterDivider} />
          <div className={styles.filterTitle}>来源场景</div>
          <Select
            allowClear
            showSearch
            optionFilterProp='label'
            aria-label='来源场景'
            placeholder='全部场景'
            className='w-full'
            options={sceneOptions}
            value={scene}
            onChange={setScene}
          />
        </aside>
        <div
          className={`${styles.canvas} ${spatial ? styles.spatial : styles.flat}`}
          data-view={spatial ? 'spatial' : 'flat'}
          data-zoom-tier='labels'
        >
          {spatial && (
            <div ref={depthPlanesRef} className={styles.depthPlanes} aria-hidden='true'>
              {layerPlanes.map(plane => (
                <div
                  key={plane.layer}
                  data-layer={plane.layer}
                  data-focused={graphLayer === plane.layer}
                  className={`${styles.depthPlane} ${
                    plane.depth === 0
                      ? styles.businessPlane
                      : plane.depth === 1
                        ? styles.analysisPlane
                        : styles.mappingPlane
                  }`}
                  style={
                    {
                      width: plane.width,
                      height: plane.height,
                      '--plane-x': `${plane.x}px`,
                      '--plane-y': `${plane.y}px`,
                    } as CSSProperties
                  }
                >
                  <span>{plane.label}</span>
                </div>
              ))}
            </div>
          )}
          <div className='absolute right-3 top-3 z-20 flex items-center gap-1 rounded-md border border-gray-200 bg-white/95 p-1 shadow-sm backdrop-blur dark:border-white/10 dark:bg-[#1a1b1e]/95'>
            <Segmented<'spatial' | 'flat'>
              size='small'
              value={spatial ? 'spatial' : 'flat'}
              onChange={value => setSpatial(value === 'spatial')}
              options={[
                { value: 'spatial', label: '分层视图' },
                { value: 'flat', label: '平面视图' },
              ]}
            />
            <Tooltip title='定位搜索结果'>
              <Button
                type='text'
                icon={<SearchOutlined />}
                disabled={!searchResults.length}
                onClick={locateFirstSearchMatch}
              />
            </Tooltip>
            <Tooltip title='适应画布'>
              <Button type='text' icon={<AimOutlined />} onClick={fitView} />
            </Tooltip>
            <Tooltip title={neighborhoodOnly ? '显示全部本体' : '仅显示选中节点的一跳邻域'}>
              <Button
                type={neighborhoodOnly ? 'primary' : 'text'}
                icon={<BranchesOutlined />}
                loading={neighborhoodLoading}
                disabled={selected?.kind !== 'node' || neighborhoodLoading}
                onClick={toggleNeighborhood}
              />
            </Tooltip>
            <Tooltip title='重新自动布局'>
              <Button type='text' icon={<ReloadOutlined />} onClick={runAutoLayout} />
            </Tooltip>
            <Tooltip title={layoutDirty ? '保存当前节点布局' : '保存节点布局'}>
              <Button type='text' loading={saving} icon={<SaveOutlined />} onClick={() => void saveLayout()} />
            </Tooltip>
            <Tooltip title={fullscreen ? '退出全屏' : '全屏'}>
              <Button
                type='text'
                icon={fullscreen ? <CompressOutlined /> : <FullscreenOutlined />}
                onClick={() => void toggleFullscreen()}
              />
            </Tooltip>
          </div>

          <ReactFlow
            className='ontology-react-flow'
            nodes={renderedNodes}
            edges={renderedEdges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            minZoom={0.18}
            maxZoom={2.4}
            fitView
            fitViewOptions={{ padding: 0.18, maxZoom: 1.2 }}
            nodesDraggable
            nodesConnectable={false}
            elementsSelectable
            panOnScroll
            selectionOnDrag={false}
            proOptions={{ hideAttribution: true }}
            onNodesChange={onNodesChange}
            onMove={onMove}
            onNodeDragStop={(_, node) => {
              workingPositions.current[layoutKey] ??= {};
              workingPositions.current[layoutKey][node.id] = node.position;
              setLayoutDirty(true);
            }}
            onNodeClick={(_, node) => {
              setSelected({ kind: 'node', value: node.data.raw });
            }}
            onNodeDoubleClick={(_, node) => {
              void loadNeighborhood(node.data.raw);
            }}
            onNodeMouseEnter={(_, node) => setHovered({ kind: 'node', value: node.data.raw })}
            onNodeMouseLeave={() => setHovered(undefined)}
            onEdgeMouseEnter={(_, edge) => {
              if (edge.data?.raw) setHovered({ kind: 'edge', value: edge.data.raw });
            }}
            onEdgeMouseLeave={() => setHovered(undefined)}
            onEdgeClick={(_, edge) => {
              const raw = edge.data?.raw;
              if (raw) {
                setSelected({ kind: 'edge', value: raw });
              }
            }}
            onPaneClick={() => {
              setSelected(undefined);
              setHovered(undefined);
            }}
          >
            <Background color='var(--ontology-grid)' gap={18} size={1} />
            <Controls showInteractive={false} position='bottom-right' />
          </ReactFlow>

          {empty && (
            <div className='pointer-events-none absolute inset-0 z-10 flex items-center justify-center'>
              <Empty description='没有与当前筛选条件匹配的本体节点' />
            </div>
          )}

          <div className={styles.graphLegend}>
            <span>
              <i />
              显式关系
            </span>
            <span>
              <i className={styles.referenceLine} />
              属性引用或已确认规则引用
            </span>
            <span>
              <i className={styles.candidateLine} />
              规则候选
            </span>
          </div>
        </div>
        <aside className={styles.detailPanel} aria-label='对象详情'>
          {!selected && (
            <div className={styles.detailEmpty}>
              <strong>对象详情</strong>
              <p>选择节点或关系查看详情</p>
            </div>
          )}
          {selected && (
            <>
              <div className='mb-3 flex items-center justify-between gap-3'>
                <div className='flex min-w-0 items-center gap-2'>
                  {selectedType && <Tag color={selectedTagColor}>{selectedTagLabel}</Tag>}
                  <strong className='truncate text-sm'>
                    {selected.kind === 'node' ? selected.value.name : selected.value.name}
                  </strong>
                </div>
                <Button
                  type='text'
                  size='small'
                  icon={<CloseOutlined />}
                  onClick={() => {
                    setSelected(undefined);
                  }}
                />
              </div>
              {selected.kind === 'node' && (
                <>
                  <p className={styles.nodeDescription}>{selected.value.description || '暂无定义'}</p>
                  <Button
                    block
                    onClick={() => void loadNeighborhood(selected.value as OntologyNode)}
                    loading={neighborhoodLoading}
                    disabled={neighborhoodOnly && neighborhoodCenter === selected.value.id}
                  >
                    查看一跳邻域
                  </Button>
                  <div className={styles.relationHeading}>
                    关系 <span>{selectedRelations.length}</span>
                  </div>
                  <div className={styles.relationList}>
                    {['显式关系', '属性引用', '规则引用'].map(origin => {
                      const relations = selectedRelations.filter(edge => edgeOrigin(edge) === origin);
                      if (!relations.length) return null;
                      return (
                        <div key={origin}>
                          <div className={styles.relationOrigin}>{origin}</div>
                          {relations.map(edge => {
                            const outgoing = edge.source === selected.value.id;
                            const otherId = outgoing ? edge.target : edge.source;
                            const other = nodeById.get(otherId);
                            return (
                              <div key={edge.id} className={styles.relationRow}>
                                <button
                                  type='button'
                                  onClick={() => other && setSelected({ kind: 'node', value: other })}
                                  disabled={!other}
                                >
                                  {other?.name || otherId}
                                </button>
                                <button type='button' onClick={() => setSelected({ kind: 'edge', value: edge })}>
                                  {outgoing ? '→' : '←'} {edge.name}
                                  {edge.type === 'rule_reference' &&
                                    (edge.data?.review_status === 'confirmed' ? ' · 已确认' : ' · 待确认')}
                                </button>
                              </div>
                            );
                          })}
                        </div>
                      );
                    })}
                    {!selectedRelations.length && <p>当前视图中没有关联关系</p>}
                  </div>
                </>
              )}
              {selected.value.type === 'metric' && (
                <div className='mb-4'>
                  <OntologyReviewDetails object={selected.value} />
                </div>
              )}
              {selectedReview && (
                <div className={`${styles.reviewPanel} mb-3 rounded-lg border p-3`}>
                  <div className='mb-2 flex items-center justify-between gap-2'>
                    <span className='text-xs font-medium text-slate-600 dark:text-slate-300'>人工审核</span>
                    <Tag
                      color={
                        selectedReview.status === 'confirmed'
                          ? 'green'
                          : selectedReview.status === 'rejected'
                            ? 'red'
                            : 'gold'
                      }
                    >
                      {selectedReview.status === 'confirmed'
                        ? '已确认'
                        : selectedReview.status === 'rejected'
                          ? '已驳回'
                          : selectedReview.required || selectedReview.object_type === 'rule_reference'
                            ? '待审核'
                            : '未审阅'}
                      {selectedReview.required ? ' · 发布必审' : ''}
                    </Tag>
                  </div>
                  {selectedReview.reviewer_id && (
                    <div className='mb-2 text-[11px] text-slate-500'>
                      {selectedReview.reviewer_id}
                      {selectedReview.reviewed_at ? ` · ${new Date(selectedReview.reviewed_at).toLocaleString()}` : ''}
                    </div>
                  )}
                  {onReviewObject && !hasUnsavedGraph && (
                    <>
                      <Input.TextArea
                        aria-label='审核意见'
                        autoSize={{ minRows: 2, maxRows: 4 }}
                        maxLength={2000}
                        placeholder='审核意见（驳回时请填写）'
                        value={reviewComment}
                        onChange={event => setReviewComment(event.target.value)}
                      />
                      <div className='mt-2 flex flex-wrap gap-2'>
                        <Button
                          size='small'
                          type='primary'
                          icon={<CheckCircleFilled />}
                          disabled={selectedReview.status === 'confirmed'}
                          onClick={() => void onReviewObject(selectedReview, 'confirmed', reviewComment)}
                        >
                          确认
                        </Button>
                        <Button
                          size='small'
                          danger
                          icon={<CloseCircleFilled />}
                          disabled={selectedReview.status === 'rejected' || !reviewComment.trim()}
                          onClick={() => void onReviewObject(selectedReview, 'rejected', reviewComment)}
                        >
                          驳回
                        </Button>
                        {selectedReview.status !== 'candidate' && (
                          <Button
                            size='small'
                            onClick={() => void onReviewObject(selectedReview, 'candidate', '撤回审核结论')}
                          >
                            撤回结论
                          </Button>
                        )}
                      </div>
                    </>
                  )}
                  {selectedReview.comment && (
                    <div className='mt-2 rounded-md bg-slate-50 px-2.5 py-2 text-xs text-slate-600 dark:bg-white/5 dark:text-slate-300'>
                      {selectedReview.comment}
                    </div>
                  )}
                  <div className='mt-3 border-t border-slate-200/70 pt-2 dark:border-white/10'>
                    <div className='mb-2 text-[11px] font-medium text-slate-500'>审核历史</div>
                    {selectedReviewEvents.length ? (
                      <div className='space-y-2'>
                        {selectedReviewEvents.map(event => (
                          <div key={event.event_id} className='text-[11px] text-slate-500'>
                            <div className='flex items-center justify-between gap-2'>
                              <span className='font-medium text-slate-700 dark:text-slate-300'>
                                {event.decision === 'confirmed'
                                  ? '确认'
                                  : event.decision === 'rejected'
                                    ? '驳回'
                                    : '撤回结论'}{' '}
                                · {event.actor_id}
                              </span>
                              <time>{new Date(event.created_at).toLocaleString()}</time>
                            </div>
                            {event.comment && <div className='mt-0.5 whitespace-pre-wrap'>{event.comment}</div>}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className='text-[11px] text-slate-400'>暂无审核记录</div>
                    )}
                  </div>
                </div>
              )}
              {selected.kind === 'node' && (
                <Space direction='vertical' className='w-full' size='middle'>
                  <Descriptions size='small' column={1}>
                    <Descriptions.Item label='ID'>{selected.value.id}</Descriptions.Item>
                    <Descriptions.Item label='别名'>{selected.value.aliases?.join('、') || '无'}</Descriptions.Item>
                    <Descriptions.Item label='定义'>{selected.value.description || '未填写'}</Descriptions.Item>
                    <Descriptions.Item label='来源场景'>{nodeSourceLabel(selected.value)}</Descriptions.Item>
                    {selected.value.provenance?.length ? (
                      <Descriptions.Item label='证据来源'>
                        <div className='space-y-2'>
                          {selected.value.provenance.map((source, index) => (
                            <div
                              key={`${source.scene_id}-${source.snapshot_id}-${index}`}
                              className='rounded-md bg-slate-50 p-2 text-xs dark:bg-white/5'
                            >
                              <div className='font-medium'>{source.scene_id || '人工维护'}</div>
                              {source.snapshot_id && (
                                <div className='mt-1 break-all text-slate-500'>快照：{source.snapshot_id}</div>
                              )}
                              {source.section && <div className='mt-1'>章节：{source.section}</div>}
                              {source.quote && (
                                <div className='mt-1 whitespace-pre-wrap text-slate-600 dark:text-slate-300'>
                                  {source.quote}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </Descriptions.Item>
                    ) : null}
                    <Descriptions.Item label='扩展属性'>
                      <pre className='m-0 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-slate-100 p-2 text-xs dark:bg-black/20'>
                        {stringifyData(selected.value.data)}
                      </pre>
                    </Descriptions.Item>
                  </Descriptions>
                </Space>
              )}
              {selected.kind === 'edge' && (
                <Descriptions size='small' column={1}>
                  <Descriptions.Item label='ID'>{selected.value.id}</Descriptions.Item>
                  <Descriptions.Item label='关系'>{selected.value.name}</Descriptions.Item>
                  <Descriptions.Item label='连线来源'>{edgeOrigin(selected.value)}</Descriptions.Item>
                  {selected.value.data?.reference_field != null && (
                    <Descriptions.Item label='引用字段'>
                      {textValue(selected.value.data.reference_field)}
                    </Descriptions.Item>
                  )}
                  <Descriptions.Item label='主体'>
                    {nodeById.get(selected.value.source)?.name || selected.value.source}
                  </Descriptions.Item>
                  <Descriptions.Item label='客体'>
                    {nodeById.get(selected.value.target)?.name || selected.value.target}
                  </Descriptions.Item>
                  <Descriptions.Item label={selected.value.type === 'rule_reference' ? '规则原文' : '说明'}>
                    {selected.value.description || '未填写'}
                  </Descriptions.Item>
                  <Descriptions.Item label='基数'>
                    {textValue(selected.value.data?.cardinality, '未填写')}
                  </Descriptions.Item>
                  <Descriptions.Item label='关联粒度'>
                    {textValue(selected.value.data?.grain, '未填写')}
                  </Descriptions.Item>
                  <Descriptions.Item label='来源'>
                    {selected.value.provenance?.map(item => `${item.scene_id} / ${item.snapshot_id}`).join('；') ||
                      '人工维护'}
                  </Descriptions.Item>
                  {selected.value.provenance?.some(item => item.section || item.quote) ? (
                    <Descriptions.Item label='证据'>
                      {selected.value.provenance
                        .map(item => [item.section, item.quote].filter(Boolean).join('：'))
                        .filter(Boolean)
                        .join('；')}
                    </Descriptions.Item>
                  ) : null}
                </Descriptions>
              )}
            </>
          )}
        </aside>
      </div>

      <div className={styles.footer}>
        <span>
          {graphPayload.visibleNodeCount}/{graphPayload.totalNodeCount} 节点 · {graphPayload.visibleEdgeCount}/
          {graphPayload.totalEdgeCount} 关系{graphPayload.hasSavedLayout ? ' · 已加载布局' : ''}
          {layoutDirty ? ' · 布局未保存' : ''}
        </span>
        <span>拖动画布平移 · 双击节点查看邻域</span>
      </div>
    </div>
  );
}

export default function OntologyGraphCanvas(props: OntologyGraphCanvasProps) {
  return (
    <ReactFlowProvider>
      <OntologyGraphInner {...props} />
    </ReactFlowProvider>
  );
}
