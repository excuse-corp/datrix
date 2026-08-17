import type { OntologyEdge, OntologyGraph, OntologyNode } from '@/utils/ontology';
import {
  AimOutlined,
  BranchesOutlined,
  CloseOutlined,
  CompressOutlined,
  FilterOutlined,
  FullscreenOutlined,
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { Button, Checkbox, Descriptions, Empty, Input, Select, Space, Tag, Tooltip } from 'antd';
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
} from 'reactflow';

const NODE_WIDTH = 190;
const NODE_HEIGHT = 72;
const COLUMN_GAP = 280;
const ROW_GAP = 104;
const EMPTY_LAYOUT: Record<string, unknown> = {};

const COLOR: Record<OntologyNode['type'], string> = {
  entity: '#2563eb',
  metric: '#059669',
  scene: '#7c3aed',
  rule: '#ea580c',
};

const EDGE_COLOR: Record<string, string> = {
  relation: '#64748b',
  belongs_to: '#2563eb',
  provided_by: '#059669',
  scene_binding: '#7c3aed',
  cross_scene_join: '#dc2626',
  clarification_rule: '#ea580c',
};

const TYPE_LABEL: Record<OntologyNode['type'], string> = {
  entity: '实体',
  metric: '指标',
  scene: '场景',
  rule: '澄清规则',
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
};

type FlowEdgeData = {
  raw: OntologyEdge;
};

type FlowNode = Node<FlowNodeData>;
type FlowEdge = Edge<FlowEdgeData>;

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

function buildDefaultPositions(nodes: OntologyNode[]) {
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
  const defaultPositions = buildDefaultPositions(visibleRawNodes);
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
    labelStyle: { fill: '#475569', fontSize: 11, fontWeight: 500 },
    labelBgStyle: { fill: '#ffffff', fillOpacity: 0.9 },
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
  const borderColor = data.searchMatched ? '#f59e0b' : data.highlighted ? '#0f172a' : data.color;
  const opacity = data.dimmed ? 0.22 : 1;

  return (
    <div
      className='group h-[72px] w-[190px] overflow-hidden rounded-md border bg-white shadow-sm transition-shadow duration-150 hover:shadow-md dark:bg-[#1a1b1e]'
      style={{ borderColor, opacity }}
    >
      <Handle type='target' position={Position.Left} isConnectable={false} className='!h-2 !w-2 !border-0 !opacity-0' />
      <Handle
        type='source'
        position={Position.Right}
        isConnectable={false}
        className='!h-2 !w-2 !border-0 !opacity-0'
      />
      <div className='flex h-full flex-col justify-between px-3 py-2'>
        <div className='flex min-w-0 items-center gap-2'>
          <span className='h-2.5 w-2.5 shrink-0 rounded-sm' style={{ backgroundColor: data.color }} />
          <span className='truncate text-sm font-semibold text-slate-900 dark:text-slate-100'>{data.label}</span>
        </div>
        <div className='truncate text-xs text-slate-500 dark:text-slate-400'>
          {data.description || data.aliases.join('、') || data.raw.id}
        </div>
        <div className='flex items-center justify-between gap-2 text-[11px]'>
          <span className='rounded px-1.5 py-0.5 font-medium text-white' style={{ backgroundColor: data.color }}>
            {data.typeLabel}
          </span>
          <span className='truncate text-slate-400'>{data.relationCount} 条关系</span>
        </div>
      </div>
    </div>
  );
}

const nodeTypes = { ontologyNode: OntologyNodeCard };

function OntologyGraphInner({ graph, layout = EMPTY_LAYOUT, onSaveLayout }: OntologyGraphCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef('');
  const reactFlow = useReactFlow<FlowNodeData, FlowEdgeData>();
  const [nodes, setNodes, onNodesChangeBase] = useNodesState<FlowNodeData>([]);
  const [edges, setEdges] = useEdgesState<FlowEdgeData>([]);
  const [types, setTypes] = useState<OntologyNode['type'][]>(['entity', 'metric', 'scene', 'rule']);
  const [scene, setScene] = useState<string>();
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<SelectedElement>();
  const [fullscreen, setFullscreen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [layoutDirty, setLayoutDirty] = useState(false);

  const sceneOptions = useMemo(
    () =>
      (graph?.nodes ?? []).filter(node => node.type === 'scene').map(node => ({ value: node.id, label: node.name })),
    [graph],
  );

  const edgeTypes = useMemo(() => Array.from(new Set((graph?.edges ?? []).map(edge => edge.type))).sort(), [graph]);

  const graphPayload = useMemo(() => buildGraphPayload(graph, layout, types, scene), [graph, layout, scene, types]);

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
      const color = raw ? (EDGE_COLOR[raw.type] ?? '#94a3b8') : '#94a3b8';
      const highlighted = !selected || focusSets.edgeIds.has(edge.id);
      return {
        ...edge,
        animated: Boolean(selected && highlighted),
        markerEnd: { type: MarkerType.ArrowClosed, color },
        style: {
          stroke: color,
          strokeWidth: selected && highlighted ? 2.5 : 1.5,
          opacity: selected && !highlighted ? 0.18 : 1,
        },
        labelStyle: {
          fill: selected && !highlighted ? '#94a3b8' : '#475569',
          fontSize: 11,
          fontWeight: selected && highlighted ? 700 : 500,
          opacity: selected && !highlighted ? 0.35 : 1,
        },
      } satisfies FlowEdge;
    });
  }, [edges, focusSets.edgeIds, selected]);

  useEffect(() => {
    setNodes(graphPayload.nodes);
    setEdges(graphPayload.edges);
    setSelected(undefined);
    setLayoutDirty(false);
    window.requestAnimationFrame(() => {
      reactFlow.fitView({ padding: 0.18, duration: 180 });
    });
  }, [graphPayload, reactFlow, setEdges, setNodes]);

  useEffect(() => {
    if (!selected) return;
    const visible = selected.kind === 'node' ? graphPayload.visibleNodeIds : graphPayload.visibleEdgeIds;
    const id = selected.kind === 'node' ? selected.value.id : selected.value.id;
    if (!visible.has(id)) setSelected(undefined);
  }, [graphPayload.visibleEdgeIds, graphPayload.visibleNodeIds, selected]);

  useEffect(() => {
    searchRef.current = search;
  }, [search]);

  useEffect(() => {
    const onFullscreenChange = () => {
      setFullscreen(Boolean(document.fullscreenElement));
      window.requestAnimationFrame(() => reactFlow.fitView({ padding: 0.18, duration: 180 }));
    };
    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, [reactFlow]);

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
    const positions = buildDefaultPositions(graphPayload.visibleRawNodes);
    setNodes(current =>
      current.map(node => ({
        ...node,
        position: positions[node.id] ?? node.position,
      })),
    );
    setLayoutDirty(true);
    window.requestAnimationFrame(fitView);
  }, [fitView, graphPayload.visibleRawNodes, setNodes]);

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

  return (
    <div ref={containerRef} className='relative h-full overflow-hidden rounded-md bg-slate-50 dark:bg-[#111217]'>
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
        onNodeDragStop={() => setLayoutDirty(true)}
        onNodeClick={(_, node) => setSelected({ kind: 'node', value: node.data.raw })}
        onEdgeClick={(_, edge) => {
          const raw = edge.data?.raw;
          if (raw) setSelected({ kind: 'edge', value: raw });
        }}
        onPaneClick={() => setSelected(undefined)}
      >
        <Background color='#dbe3ef' gap={18} size={1} />
        <Controls showInteractive={false} position='bottom-right' />
        <MiniMap
          pannable
          zoomable
          position='bottom-right'
          nodeColor={node => node.data?.color ?? '#94a3b8'}
          maskColor='rgba(15, 23, 42, 0.08)'
          style={{ bottom: 46, border: '1px solid #e5e7eb', borderRadius: 6 }}
        />
      </ReactFlow>

      {empty && (
        <div className='pointer-events-none absolute inset-0 z-10 flex items-center justify-center'>
          <Empty description='没有与当前筛选条件匹配的本体节点' />
        </div>
      )}

      {selected && (
        <aside className='absolute bottom-3 right-3 top-16 z-20 w-[340px] overflow-auto rounded-md border border-gray-200 bg-white/95 p-4 shadow-lg backdrop-blur dark:border-white/10 dark:bg-[#1a1b1e]/95'>
          <div className='mb-3 flex items-center justify-between gap-3'>
            <div className='flex min-w-0 items-center gap-2'>
              {selectedType && <Tag color={selectedTagColor}>{selectedTagLabel}</Tag>}
              <strong className='truncate text-sm'>
                {selected.kind === 'node' ? selected.value.name : selected.value.name}
              </strong>
            </div>
            <Button type='text' size='small' icon={<CloseOutlined />} onClick={() => setSelected(undefined)} />
          </div>
          {selected.kind === 'node' && (
            <Space direction='vertical' className='w-full' size='middle'>
              <Descriptions size='small' column={1}>
                <Descriptions.Item label='ID'>{selected.value.id}</Descriptions.Item>
                <Descriptions.Item label='别名'>{selected.value.aliases?.join('、') || '无'}</Descriptions.Item>
                <Descriptions.Item label='定义'>{selected.value.description || '未填写'}</Descriptions.Item>
                <Descriptions.Item label='来源场景'>{nodeSourceLabel(selected.value)}</Descriptions.Item>
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
        {edgeTypes.map(type => (
          <span key={type} className='inline-flex items-center gap-1'>
            <span className='h-2 w-2 rounded-full' style={{ background: EDGE_COLOR[type] ?? '#94a3b8' }} />
            {EDGE_LABEL[type] || type}
          </span>
        ))}
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
