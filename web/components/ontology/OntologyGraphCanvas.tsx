import type { OntologyEdge, OntologyGraph, OntologyNode } from '@/utils/ontology';
import {
  AimOutlined,
  CompressOutlined,
  FilterOutlined,
  FullscreenOutlined,
  SaveOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import type { Graph, GraphData, GraphOptions } from '@antv/g6';
import { Graphin } from '@antv/graphin';
import { Button, Checkbox, Descriptions, Drawer, Empty, Input, Select, Space, Tag, Tooltip } from 'antd';
import { useMemo, useRef, useState } from 'react';

const COLOR: Record<string, string> = {
  entity: '#2563eb',
  metric: '#059669',
  scene: '#7c3aed',
  rule: '#ea580c',
};

const TYPE_LABEL: Record<OntologyNode['type'], string> = {
  entity: '实体',
  metric: '指标',
  scene: '场景',
  rule: '澄清规则',
};

type OntologyGraphCanvasProps = {
  graph?: OntologyGraph;
  layout?: Record<string, unknown>;
  onSaveLayout?: (layout: Record<string, unknown>) => Promise<void> | void;
};

type SelectedElement = { kind: 'node'; value: OntologyNode } | { kind: 'edge'; value: OntologyEdge };

export default function OntologyGraphCanvas({ graph, layout = {}, onSaveLayout }: OntologyGraphCanvasProps) {
  const graphRef = useRef<Graph | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [types, setTypes] = useState<OntologyNode['type'][]>(['entity', 'metric', 'scene', 'rule']);
  const [scene, setScene] = useState<string>();
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<SelectedElement>();
  const [fullscreen, setFullscreen] = useState(false);
  const [saving, setSaving] = useState(false);

  const sceneOptions = useMemo(
    () =>
      (graph?.nodes ?? []).filter(node => node.type === 'scene').map(node => ({ value: node.id, label: node.name })),
    [graph],
  );

  const graphData = useMemo<GraphData>(() => {
    if (!graph) return { nodes: [], edges: [] };
    const query = search.trim().toLowerCase();
    const sceneIds = new Set<string>();
    if (scene) {
      sceneIds.add(scene);
      graph.edges.forEach(edge => {
        if (edge.source === scene || edge.target === scene || edge.data?.source_scene === scene) {
          sceneIds.add(edge.source);
          sceneIds.add(edge.target);
        }
      });
      graph.nodes.forEach(node => {
        if (node.data?.source_scene === scene) sceneIds.add(node.id);
      });
    }
    const nodes = graph.nodes.filter(node => {
      const matchesType = types.includes(node.type);
      const matchesScene = !scene || sceneIds.has(node.id);
      const haystack = `${node.id} ${node.name} ${(node.aliases ?? []).join(' ')}`.toLowerCase();
      return matchesType && matchesScene && (!query || haystack.includes(query));
    });
    const nodeIds = new Set(nodes.map(node => node.id));
    return {
      nodes: nodes.map(node => {
        const position = layout[node.id];
        return {
          id: node.id,
          data: node,
          style: typeof position === 'object' && position !== null ? position : undefined,
        };
      }),
      edges: graph.edges
        .filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target))
        .map(edge => ({ id: edge.id, source: edge.source, target: edge.target, data: edge })),
    };
  }, [graph, layout, scene, search, types]);

  const options = useMemo<GraphOptions>(
    () => ({
      data: graphData,
      autoFit: 'center',
      animation: false,
      node: {
        style: datum => ({
          size: [Math.max(112, Math.min(String(datum.data?.name ?? '').length * 18 + 36, 220)), 38],
          radius: 6,
          fill: COLOR[String(datum.data?.type)] ?? '#64748b',
          stroke: '#fff',
          lineWidth: 2,
          label: true,
          labelText: String(datum.data?.name ?? datum.id),
          labelFill: '#fff',
          labelFontSize: 12,
          labelFontWeight: 500,
        }),
      },
      edge: {
        style: datum => ({
          stroke: '#94a3b8',
          lineWidth: 1.25,
          endArrow: true,
          endArrowType: 'vee',
          label: true,
          labelText: String(datum.data?.name ?? ''),
          labelFill: '#475569',
          labelFontSize: 10,
          labelBackground: true,
          labelBackgroundFill: '#fff',
          labelPadding: [2, 4],
          labelBackgroundRadius: 3,
        }),
      },
      behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element', 'click-select'],
      layout: {
        type: 'force',
        preventOverlap: true,
        nodeSize: 130,
        linkDistance: 170,
      },
    }),
    [graphData],
  );

  const saveLayout = async () => {
    if (!graphRef.current || !onSaveLayout) return;
    setSaving(true);
    try {
      const saved = Object.fromEntries(
        graphRef.current.getNodeData().map(node => {
          const position = graphRef.current?.getElementPosition(node.id) as unknown;
          const value = Array.isArray(position) ? { x: position[0], y: position[1] } : position;
          return [node.id, value];
        }),
      );
      await onSaveLayout(saved);
    } finally {
      setSaving(false);
    }
  };

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await containerRef.current?.requestFullscreen();
      setFullscreen(Boolean(document.fullscreenElement));
      window.setTimeout(() => graphRef.current?.resize(), 50);
    } catch {
      setFullscreen(Boolean(document.fullscreenElement));
    }
  };

  if (!graphData.nodes?.length) {
    return <Empty className='flex h-full flex-col justify-center' description='没有与当前筛选条件匹配的本体节点' />;
  }

  return (
    <div ref={containerRef} className='relative h-full overflow-hidden bg-slate-50 dark:bg-[#111217]'>
      <div className='absolute left-3 top-3 z-10 flex max-w-[calc(100%-24px)] flex-wrap items-center gap-2 rounded-md border border-gray-200 bg-white/95 p-2 shadow-sm dark:border-white/10 dark:bg-[#1a1b1e]/95'>
        <Input
          allowClear
          aria-label='搜索本体节点'
          className='w-40'
          prefix={<SearchOutlined />}
          placeholder='搜索实体或指标'
          value={search}
          onChange={event => setSearch(event.target.value)}
        />
        <Select
          allowClear
          className='w-32'
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
      <div className='absolute right-3 top-3 z-10 flex items-center gap-1 rounded-md border border-gray-200 bg-white/95 p-1 shadow-sm dark:border-white/10 dark:bg-[#1a1b1e]/95'>
        <Tooltip title='适应画布'>
          <Button type='text' icon={<AimOutlined />} onClick={() => void graphRef.current?.fitView()} />
        </Tooltip>
        <Tooltip title='保存节点布局'>
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
      <div className='absolute bottom-3 left-3 z-10 flex items-center gap-2 text-xs text-gray-500'>
        <FilterOutlined />
        <span>选择节点或关系查看业务定义和来源</span>
      </div>
      <Graphin
        style={{ height: '100%', width: '100%' }}
        options={options}
        onReady={instance => {
          graphRef.current = instance;
          instance.on('node:click', event => {
            const id = (event as { target?: { id?: string } }).target?.id;
            if (!id) return;
            const datum = instance.getNodeData(id);
            if (datum?.data) setSelected({ kind: 'node', value: datum.data as OntologyNode });
          });
          instance.on('edge:click', event => {
            const id = (event as { target?: { id?: string } }).target?.id;
            if (!id) return;
            const datum = instance.getEdgeData(id);
            if (datum?.data) setSelected({ kind: 'edge', value: datum.data as OntologyEdge });
          });
        }}
      />
      <Drawer
        title={selected?.kind === 'node' ? '本体对象' : '本体关系'}
        open={Boolean(selected)}
        onClose={() => setSelected(undefined)}
        width={360}
      >
        {selected?.kind === 'node' && (
          <Space direction='vertical' className='w-full' size='middle'>
            <div className='flex items-center gap-2'>
              <Tag color={COLOR[selected.value.type]}>{TYPE_LABEL[selected.value.type]}</Tag>
              <strong>{selected.value.name}</strong>
            </div>
            <Descriptions size='small' column={1}>
              <Descriptions.Item label='ID'>{selected.value.id}</Descriptions.Item>
              <Descriptions.Item label='别名'>{selected.value.aliases?.join('、') || '无'}</Descriptions.Item>
              <Descriptions.Item label='定义'>{selected.value.description || '未填写'}</Descriptions.Item>
              <Descriptions.Item label='来源场景'>
                {selected.value.provenance?.map(item => item.scene_id).join('、') ||
                  String(selected.value.data?.source_scene ?? '人工维护')}
              </Descriptions.Item>
            </Descriptions>
          </Space>
        )}
        {selected?.kind === 'edge' && (
          <Descriptions size='small' column={1}>
            <Descriptions.Item label='ID'>{selected.value.id}</Descriptions.Item>
            <Descriptions.Item label='关系'>{selected.value.name}</Descriptions.Item>
            <Descriptions.Item label='主体'>{selected.value.source}</Descriptions.Item>
            <Descriptions.Item label='客体'>{selected.value.target}</Descriptions.Item>
            <Descriptions.Item label='说明'>{selected.value.description || '未填写'}</Descriptions.Item>
            <Descriptions.Item label='基数'>{String(selected.value.data?.cardinality ?? '未填写')}</Descriptions.Item>
            <Descriptions.Item label='关联粒度'>{String(selected.value.data?.grain ?? '未填写')}</Descriptions.Item>
            <Descriptions.Item label='来源'>
              {selected.value.provenance?.map(item => `${item.scene_id} / ${item.snapshot_id}`).join('；') ||
                '人工维护'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </div>
  );
}
