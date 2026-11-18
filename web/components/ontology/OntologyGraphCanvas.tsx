import type {
  OntologyEdge,
  OntologyGraph,
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
  FilterOutlined,
  FullscreenOutlined,
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { Button, Checkbox, Descriptions, Empty, Input, Segmented, Select, Space, Tag, Tooltip } from 'antd';
import type { CSSProperties } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type OnNodesChange,
  type Viewport,
} from 'reactflow';
import styles from './OntologyGraphCanvas.module.css';
import OntologyReviewDetails from './OntologyReviewDetails';

const NODE_WIDTH = 220;
const NODE_HEIGHT = 96;
const COLUMN_GAP = 300;
const ROW_GAP = 126;
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

const TYPE_ORDER: OntologyNode['type'][] = ['scene', 'entity', 'metric', 'rule'];

type OntologyGraphCanvasProps = {
  graph?: OntologyGraph;
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
};

type SelectedElement = { kind: 'node'; value: OntologyNode } | { kind: 'edge'; value: OntologyEdge };

type FlowNodeData = {
  raw: OntologyNode;
  label: string;
  typeLabel: string;
  color: string;
  aliases: string[];
  description: string;
  sourceLabel: string;
  relationCount: number;
  highlighted: boolean;
  dimmed: boolean;
  searchMatched: boolean;
  reviewStatus: string;
  reviewRequired: boolean;
  spatial: boolean;
  depth: number;
};

type FlowEdgeData = {
  raw: OntologyEdge;
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

function buildDefaultPositions(nodes: OntologyNode[], spatial = false) {
  if (spatial) {
    const layers = ['business_semantics', 'analysis_explanations', 'mapping_governance'];
    const positions: Record<string, { x: number; y: number }> = {};
    const xOffset = ((layers.length - 1) * 430) / 2;
    layers.forEach((layer, layerIndex) => {
      const group = nodes
        .filter(node => nodeLayer(node.type) === layer)
        .sort((left, right) => {
          const typeOrder = TYPE_ORDER.indexOf(left.type) - TYPE_ORDER.indexOf(right.type);
          return typeOrder || sortNodeForLayout(left, right);
        });
      const yOffset = ((group.length - 1) * ROW_GAP) / 2;
      group.forEach((node, nodeIndex) => {
        positions[node.id] = {
          x: layerIndex * 430 - xOffset,
          y: nodeIndex * ROW_GAP - yOffset + (layerIndex - 1) * 28,
        };
      });
    });
    return positions;
  }

  const byType = new Map<OntologyNode['type'], OntologyNode[]>();
  TYPE_ORDER.forEach(type => byType.set(type, []));
  nodes.forEach(node => byType.get(node.type)?.push(node));

  const activeTypes = TYPE_ORDER.filter(type => (byType.get(type)?.length ?? 0) > 0);
  const positions: Record<string, { x: number; y: number }> = {};
  const xOffset = ((activeTypes.length - 1) * COLUMN_GAP) / 2;

  activeTypes.forEach((type, typeIndex) => {
    const group = [...(byType.get(type) ?? [])].sort(sortNodeForLayout);
    const yOffset = ((group.length - 1) * ROW_GAP) / 2;
    group.forEach((node, nodeIndex) => {
      positions[node.id] = {
        x: typeIndex * COLUMN_GAP - xOffset,
        y: nodeIndex * ROW_GAP - yOffset,
      };
    });
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
  const visibleRawNodes = graph.nodes.filter(
    node => types.includes(node.type) && (!scene || sceneNodeIds.has(node.id)),
  );
  const visibleNodeIds = new Set(visibleRawNodes.map(node => node.id));
  const visibleRawEdges = graph.edges.filter(
    edge => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
  );
  const visibleEdgeIds = new Set(visibleRawEdges.map(edge => edge.id));
  const defaultPositions = buildDefaultPositions(visibleRawNodes, spatial);
  const reviewByTarget = new Map(reviewItems.map(item => [`${item.target_type}:${item.target_id}`, item]));
  const relationCount = new Map<string, number>();
  visibleRawEdges.forEach(edge => {
    relationCount.set(edge.source, (relationCount.get(edge.source) ?? 0) + 1);
    relationCount.set(edge.target, (relationCount.get(edge.target) ?? 0) + 1);
  });

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
        typeLabel: TYPE_LABEL[node.type],
        color: COLOR[node.type] ?? '#64748b',
        aliases: node.aliases ?? [],
        description: node.description ?? '',
        sourceLabel: nodeSourceLabel(node),
        relationCount: relationCount.get(node.id) ?? 0,
        highlighted: false,
        dimmed: false,
        searchMatched: false,
        reviewStatus: reviewByTarget.get(`node:${node.id}`)?.status ?? 'candidate',
        reviewRequired: reviewByTarget.get(`node:${node.id}`)?.required ?? false,
        spatial,
        depth: ['business_semantics', 'analysis_explanations', 'mapping_governance'].indexOf(nodeLayer(node.type)),
      },
    } satisfies FlowNode;
  });

  const edges = visibleRawEdges.map(edge => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: 'smoothstep',
    label: edge.name || EDGE_LABEL[edge.type] || edge.type,
    data: { raw: edge },
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

function buildFocusSets(selected: SelectedElement | undefined, edges: FlowEdge[]) {
  const nodeIds = new Set<string>();
  const edgeIds = new Set<string>();
  if (!selected) return { nodeIds, edgeIds };

  if (selected.kind === 'node') {
    nodeIds.add(selected.value.id);
    edges.forEach(edge => {
      if (edge.source !== selected.value.id && edge.target !== selected.value.id) return;
      edgeIds.add(edge.id);
      nodeIds.add(edge.source);
      nodeIds.add(edge.target);
    });
    return { nodeIds, edgeIds };
  }

  edgeIds.add(selected.value.id);
  nodeIds.add(selected.value.source);
  nodeIds.add(selected.value.target);
  return { nodeIds, edgeIds };
}

function OntologyNodeCard({ data }: NodeProps<FlowNodeData>) {
  const borderColor = data.searchMatched
    ? 'var(--ontology-warning)'
    : data.highlighted
      ? 'var(--ontology-focus)'
      : data.color;
  const opacity = data.dimmed ? 0.22 : 1;
  const reviewLabel =
    data.reviewStatus === 'confirmed'
      ? '已确认'
      : data.reviewStatus === 'rejected'
        ? '已驳回'
        : data.reviewRequired
          ? '待审核'
          : '未审阅';
  const reviewClass =
    data.reviewStatus === 'confirmed'
      ? styles.reviewConfirmed
      : data.reviewStatus === 'rejected'
        ? styles.reviewRejected
        : styles.reviewPending;

  return (
    <div
      className={`${styles.nodeCard} group h-[96px] w-[220px] overflow-hidden rounded-xl border bg-white shadow-sm transition-all duration-200 hover:shadow-lg dark:bg-[#1a1b1e] ${
        data.spatial ? styles.spatialNode : ''
      }`}
      style={
        {
          borderColor,
          opacity,
          '--node-accent': data.color,
          transform: data.spatial
            ? `perspective(1100px) rotateX(3deg) rotateY(${data.depth === 0 ? '4deg' : data.depth === 2 ? '-4deg' : '0deg'}) translateZ(${24 - data.depth * 10}px)`
            : undefined,
        } as CSSProperties
      }
    >
      <Handle type='target' position={Position.Left} isConnectable={false} className='!h-2 !w-2 !border-0 !opacity-0' />
      <Handle
        type='source'
        position={Position.Right}
        isConnectable={false}
        className='!h-2 !w-2 !border-0 !opacity-0'
      />
      <div className={`${styles.nodeContent} flex h-full flex-col justify-between px-3 py-2`}>
        <div className='flex min-w-0 items-center gap-2'>
          <span className={`${styles.nodeMark} h-3 w-3 shrink-0 rounded`} style={{ backgroundColor: data.color }} />
          <span className='truncate text-sm font-semibold text-slate-900 dark:text-slate-100'>{data.label}</span>
        </div>
        <div className='truncate text-xs text-slate-500 dark:text-slate-400'>
          {data.description || data.aliases.join('、') || data.raw.id}
        </div>
        <div className='flex items-center justify-between gap-2 text-[11px]'>
          <span className={`${styles.typeBadge} rounded-md px-1.5 py-0.5 font-medium`} style={{ color: data.color }}>
            {data.typeLabel}
          </span>
          <span className={`${reviewClass} shrink-0 rounded-md px-1.5 py-0.5 font-medium`}>
            {reviewLabel}
            {data.reviewRequired ? ' · 必审' : ''}
          </span>
        </div>
      </div>
    </div>
  );
}

const nodeTypes = { ontologyNode: OntologyNodeCard };

function OntologyGraphInner({
  graph,
  layout = EMPTY_LAYOUT,
  onSaveLayout,
  reviewState,
  onReviewObject,
  focusTarget,
  onFocusTargetHandled,
}: OntologyGraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const depthPlanesRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef('');
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
  const [fullscreen, setFullscreen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [layoutDirty, setLayoutDirty] = useState(false);
  const [spatial, setSpatial] = useState(true);
  const [reviewComment, setReviewComment] = useState('');

  const sceneOptions = useMemo(
    () =>
      (graph?.nodes ?? []).filter(node => node.type === 'scene').map(node => ({ value: node.id, label: node.name })),
    [graph],
  );

  const edgeTypes = useMemo(() => Array.from(new Set((graph?.edges ?? []).map(edge => edge.type))).sort(), [graph]);

  const reviewItems = useMemo(() => reviewState?.items ?? [], [reviewState?.items]);
  const graphPayload = useMemo(
    () => buildGraphPayload(graph, layout, types, scene, spatial, reviewItems),
    [graph, layout, reviewItems, scene, spatial, types],
  );
  const layerPlanes = useMemo<GraphLayerPlane[]>(
    () =>
      spatial
        ? [
            ['business_semantics', '01 · 业务语义'],
            ['analysis_explanations', '02 · 分析解释'],
            ['mapping_governance', '03 · 映射治理'],
          ].flatMap(([layer, label], depth) => {
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

  const searchMatchCount = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return 0;
    return graphPayload.visibleRawNodes.filter(node => nodeSearchHaystack(node).includes(query)).length;
  }, [graphPayload.visibleRawNodes, search]);

  const focusSets = useMemo(() => buildFocusSets(selected, edges), [edges, selected]);

  const renderedNodes = useMemo(() => {
    const query = search.trim().toLowerCase();
    return nodes.map(node => {
      const searchMatched = Boolean(query && nodeSearchHaystack(node.data.raw).includes(query));
      const highlighted = !selected || focusSets.nodeIds.has(node.id);
      return {
        ...node,
        style: { width: NODE_WIDTH, height: NODE_HEIGHT },
        data: {
          ...node.data,
          highlighted,
          dimmed: Boolean(selected && !highlighted),
          searchMatched,
        },
      };
    });
  }, [focusSets.nodeIds, nodes, search, selected]);

  const renderedEdges = useMemo(() => {
    return edges.map(edge => {
      const raw = edge.data?.raw;
      const baseColor = raw ? (EDGE_COLOR[raw.type] ?? 'var(--ontology-edge)') : 'var(--ontology-edge)';
      const reviewRequired = Boolean(raw?.data?.review_required);
      const reviewStatus = String(raw?.data?.review_status ?? 'candidate');
      const color =
        reviewRequired && reviewStatus === 'rejected'
          ? 'var(--ontology-review-rejected)'
          : reviewRequired && reviewStatus !== 'confirmed'
            ? 'var(--ontology-review-pending)'
            : baseColor;
      const highlighted = !selected || focusSets.edgeIds.has(edge.id);
      return {
        ...edge,
        type: spatial || raw?.type === 'cross_scene_join' ? 'default' : 'smoothstep',
        animated: Boolean(selected && highlighted),
        markerEnd: { type: MarkerType.ArrowClosed, color },
        style: {
          stroke: color,
          strokeWidth: selected && highlighted ? 3 : raw?.type === 'cross_scene_join' ? 2.5 : 1.8,
          opacity: selected && !highlighted ? 0.16 : 0.94,
          strokeDasharray: reviewRequired && reviewStatus !== 'confirmed' ? '7 5' : undefined,
          filter: selected && highlighted ? 'drop-shadow(0 0 6px var(--ontology-focus-glow))' : undefined,
        },
        labelStyle: {
          fill: selected && !highlighted ? 'var(--ontology-edge-muted)' : 'var(--ontology-edge-label)',
          fontSize: 11,
          fontWeight: selected && highlighted ? 700 : 500,
          opacity: selected && !highlighted ? 0.35 : 1,
        },
      } satisfies FlowEdge;
    });
  }, [edges, focusSets.edgeIds, selected, spatial]);

  const updateDepthPlanes = useCallback((nextViewport: Viewport) => {
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
    setSelected(undefined);
    setLayoutDirty(false);
    window.requestAnimationFrame(() => {
      reactFlow.fitView({ padding: 0.18, duration: 180 });
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
    graphPayload.nodes,
    graphPayload.visibleRawEdges,
    onFocusTargetHandled,
    reactFlow,
    scene,
    types.length,
  ]);

  useEffect(() => {
    searchRef.current = search;
  }, [search]);

  useEffect(() => {
    const onFullscreenChange = () => {
      setFullscreen(Boolean(document.fullscreenElement));
      window.requestAnimationFrame(() => {
        reactFlow.fitView({ padding: 0.18, duration: 180 });
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
    reactFlow.fitView({ padding: 0.18, duration: 220 });
  }, [reactFlow]);

  const runAutoLayout = useCallback(() => {
    const positions = buildDefaultPositions(graphPayload.visibleRawNodes, spatial);
    setNodes(current =>
      current.map(node => ({
        ...node,
        position: positions[node.id] ?? node.position,
      })),
    );
    setLayoutDirty(true);
    window.requestAnimationFrame(fitView);
  }, [fitView, graphPayload.visibleRawNodes, setNodes, spatial]);

  const saveLayout = async () => {
    if (!onSaveLayout) return;
    setSaving(true);
    try {
      const saved: Record<string, unknown> = { ...layout };
      reactFlow.getNodes().forEach(node => {
        saved[node.id] = node.position;
      });
      await onSaveLayout(saved);
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

  const locateFirstSearchMatch = () => {
    const query = searchRef.current.trim().toLowerCase();
    if (!query) return;
    const first = reactFlow.getNodes().find(node => nodeSearchHaystack(node.data.raw).includes(query));
    if (!first) return;
    setSelected({ kind: 'node', value: first.data.raw });
    reactFlow.setCenter(first.position.x + NODE_WIDTH / 2, first.position.y + NODE_HEIGHT / 2, {
      zoom: Math.max(reactFlow.getZoom(), 1.05),
      duration: 220,
    });
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

  return (
    <div
      ref={containerRef}
      className={`${styles.canvas} ${spatial ? styles.spatial : styles.flat} relative h-full overflow-hidden rounded-xl`}
      data-view={spatial ? 'spatial' : 'flat'}
    >
      {spatial && (
        <div ref={depthPlanesRef} className={styles.depthPlanes} aria-hidden='true'>
          {layerPlanes.map(plane => (
            <div
              key={plane.layer}
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
                  '--plane-z': `${(1 - plane.depth) * 34}px`,
                } as CSSProperties
              }
            >
              <span>{plane.label}</span>
            </div>
          ))}
        </div>
      )}
      <div className='absolute left-3 top-3 z-20 flex max-w-[calc(100%-24px)] flex-wrap items-center gap-2 rounded-md border border-gray-200 bg-white/95 p-2 shadow-sm backdrop-blur dark:border-white/10 dark:bg-[#1a1b1e]/95'>
        <Input
          allowClear
          aria-label='搜索本体节点'
          className='w-44'
          prefix={<SearchOutlined />}
          placeholder='搜索实体、指标、别名'
          value={search}
          onChange={event => setSearch(event.target.value)}
          onPressEnter={locateFirstSearchMatch}
        />
        <Select
          allowClear
          className='w-36'
          placeholder='全部场景'
          options={sceneOptions}
          value={scene}
          onChange={value => setScene(value)}
        />
        <Checkbox.Group
          aria-label='节点类型筛选'
          options={Object.entries(TYPE_LABEL).map(([value, label]) => ({ value, label }))}
          value={types}
          onChange={values => setTypes(values as OntologyNode['type'][])}
        />
      </div>

      <div className='absolute right-3 top-3 z-20 flex items-center gap-1 rounded-md border border-gray-200 bg-white/95 p-1 shadow-sm backdrop-blur dark:border-white/10 dark:bg-[#1a1b1e]/95'>
        <Segmented<'spatial' | 'flat'>
          size='small'
          value={spatial ? 'spatial' : 'flat'}
          onChange={value => setSpatial(value === 'spatial')}
          options={[
            { value: 'spatial', label: '空间视图' },
            { value: 'flat', label: '平面视图' },
          ]}
        />
        <Tooltip title='定位搜索结果'>
          <Button
            type='text'
            icon={<SearchOutlined />}
            disabled={!search || !searchMatchCount}
            onClick={locateFirstSearchMatch}
          />
        </Tooltip>
        <Tooltip title='适应画布'>
          <Button type='text' icon={<AimOutlined />} onClick={fitView} />
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
        minZoom={0.18}
        maxZoom={2.4}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        panOnScroll
        selectionOnDrag={false}
        proOptions={{ hideAttribution: true }}
        onNodesChange={onNodesChange}
        onMove={onMove}
        onNodeDragStop={() => setLayoutDirty(true)}
        onNodeClick={(_, node) => setSelected({ kind: 'node', value: node.data.raw })}
        onEdgeClick={(_, edge) => {
          const raw = edge.data?.raw;
          if (raw) setSelected({ kind: 'edge', value: raw });
        }}
        onPaneClick={() => setSelected(undefined)}
      >
        <Background color='var(--ontology-grid)' gap={18} size={1} />
        <Controls showInteractive={false} position='bottom-right' />
        <MiniMap
          pannable
          zoomable
          position='bottom-right'
          nodeColor={node => node.data?.color ?? '#94a3b8'}
          maskColor='var(--ontology-minimap-mask)'
          style={{ bottom: 46, border: '1px solid var(--ontology-border)', borderRadius: 10 }}
        />
      </ReactFlow>

      {empty && (
        <div className='pointer-events-none absolute inset-0 z-10 flex items-center justify-center'>
          <Empty description='没有与当前筛选条件匹配的本体节点' />
        </div>
      )}

      {selected && (
        <aside
          className={`${styles.detailPanel} absolute bottom-3 right-3 top-16 z-20 w-[360px] overflow-auto rounded-xl border border-gray-200 bg-white/95 p-4 shadow-lg backdrop-blur dark:border-white/10 dark:bg-[#1a1b1e]/95`}
        >
          <div className='mb-3 flex items-center justify-between gap-3'>
            <div className='flex min-w-0 items-center gap-2'>
              {selectedType && <Tag color={selectedTagColor}>{selectedTagLabel}</Tag>}
              <strong className='truncate text-sm'>
                {selected.kind === 'node' ? selected.value.name : selected.value.name}
              </strong>
            </div>
            <Button type='text' size='small' icon={<CloseOutlined />} onClick={() => setSelected(undefined)} />
          </div>
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
                      : selectedReview.required
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
              {onReviewObject && (
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
              <Descriptions.Item label='主体'>{selected.value.source}</Descriptions.Item>
              <Descriptions.Item label='客体'>{selected.value.target}</Descriptions.Item>
              <Descriptions.Item label='说明'>{selected.value.description || '未填写'}</Descriptions.Item>
              <Descriptions.Item label='基数'>
                {textValue(selected.value.data?.cardinality, '未填写')}
              </Descriptions.Item>
              <Descriptions.Item label='关联粒度'>{textValue(selected.value.data?.grain, '未填写')}</Descriptions.Item>
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
        </aside>
      )}

      <div className='absolute bottom-3 left-3 z-20 flex max-w-[calc(100%-24px)] flex-wrap items-center gap-3 rounded-md border border-gray-200 bg-white/90 px-3 py-2 text-xs text-gray-500 shadow-sm backdrop-blur dark:border-white/10 dark:bg-[#1a1b1e]/90'>
        <span className='inline-flex items-center gap-1'>
          <FilterOutlined />
          {graphPayload.visibleNodeCount}/{graphPayload.totalNodeCount} 节点，{graphPayload.visibleEdgeCount}/
          {graphPayload.totalEdgeCount} 关系
        </span>
        {reviewState && (
          <span className='inline-flex items-center gap-1'>
            <CheckCircleFilled className='text-emerald-500' />
            {reviewState.confirmed_required_count}/{reviewState.required_count} 必审对象已确认
          </span>
        )}
        {edgeTypes.map(type => (
          <span key={type} className='inline-flex items-center gap-1'>
            <span className='h-2 w-2 rounded-full' style={{ background: EDGE_COLOR[type] ?? '#94a3b8' }} />
            {EDGE_LABEL[type] || type}
          </span>
        ))}
        {reviewState?.required_count ? (
          <span className='inline-flex items-center gap-1'>
            <span className={`${styles.reviewLine} w-5 border-t-2 border-dashed`} />
            必审待确认
          </span>
        ) : null}
        {graphPayload.hasSavedLayout && (
          <span className='inline-flex items-center gap-1 text-slate-500'>
            <SaveOutlined />
            已加载布局
          </span>
        )}
        {layoutDirty && (
          <span className='inline-flex items-center gap-1 text-amber-600'>
            <BranchesOutlined />
            布局未保存
          </span>
        )}
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
