import AskDataPageShell from '@/components/ask-data/AskDataPageShell';
import {
  activateOntologySnapshot,
  buildOntologySnapshot,
  generateOntologyFromScenes,
  getOntology,
  getOntologyGraph,
  getOntologyNeo4jHealth,
  getOntologySnapshotSyncStatus,
  listOntologyRevisions,
  previewOntology,
  saveOntologyDraft,
  saveOntologyLayout,
  syncOntologySnapshot,
  validateOntologyRevision,
  type OntologyGraph,
  type OntologyIssue,
  type OntologyNeo4jHealth,
  type OntologyRevision,
  type OntologySnapshot,
  type OntologySnapshotSyncStatus,
  type OntologySourceRef,
  type OntologySourceRefresh,
} from '@/utils/ontology';
import {
  ApartmentOutlined,
  BranchesOutlined,
  CheckCircleFilled,
  CloudUploadOutlined,
  DatabaseOutlined,
  DiffOutlined,
  DownloadOutlined,
  ExclamationCircleFilled,
  FileSearchOutlined,
  ReloadOutlined,
  SaveOutlined,
  SyncOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { Alert, Button, Modal, Segmented, Spin, Tag, Tooltip, Upload, message } from 'antd';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useState } from 'react';

const MonacoEditor = dynamic(() => import('@/components/chat/monaco-editor'), { ssr: false });
const OntologyGraphCanvas = dynamic(() => import('@/components/ontology/OntologyGraphCanvas'), { ssr: false });
const DiffEditor = dynamic(() => import('@monaco-editor/react').then(module => module.DiffEditor), { ssr: false });

type ViewMode = 'split' | 'document' | 'graph';
type OntologyLayer = 'business_semantics' | 'analysis_explanations' | 'mapping_governance';
type OntologyLayerView = OntologyLayer | 'all';
type BridgeReviewItem = {
  pathId?: string;
  joinRowId: string;
  name: string;
  data: Record<string, unknown>;
};

const issueText = (issues: OntologyIssue[]) =>
  issues
    .slice(0, 3)
    .map(issue => `${issue.line ? `第 ${issue.line} 行：` : ''}${issue.message}`)
    .join('；');

const sourceName = (source?: Partial<OntologySourceRef>) => source?.scene_name || source?.scene_id || '-';

const markdownCells = (line: string) =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map(cell => cell.trim());

function updateBridgeTableStatus(markdown: string, section: string, id: string, status: string) {
  const lines = markdown.split('\n');
  const headingIndex = lines.findIndex(line => new RegExp(`^#{2,3}\\s+${section}\\s*$`).test(line));
  if (headingIndex < 0) return markdown;
  let headerIndex = headingIndex + 1;
  while (headerIndex < lines.length && !lines[headerIndex].trim().startsWith('|')) {
    if (/^#{1,3}\s+/.test(lines[headerIndex])) return markdown;
    headerIndex += 1;
  }
  if (!lines[headerIndex + 1]?.trim().startsWith('|')) return markdown;

  const headers = markdownCells(lines[headerIndex]);
  const separator = markdownCells(lines[headerIndex + 1]);
  let statusIndex = headers.findIndex(header => header.replace(/[\s_（）()：:]/g, '').toLowerCase() === '状态');
  if (statusIndex < 0) {
    const idIndex = headers.findIndex(header => header.replace(/\s/g, '') === 'ID');
    statusIndex = idIndex >= 0 ? idIndex + 1 : 0;
    headers.splice(statusIndex, 0, '状态');
    separator.splice(statusIndex, 0, '---');
    lines[headerIndex] = `| ${headers.join(' | ')} |`;
    lines[headerIndex + 1] = `| ${separator.join(' | ')} |`;
  }

  for (let index = headerIndex + 2; index < lines.length && lines[index].trim().startsWith('|'); index += 1) {
    const cells = markdownCells(lines[index]);
    if (cells[0] !== id) continue;
    while (cells.length < headers.length) cells.push('');
    cells[statusIndex] = status;
    lines[index] = `| ${cells.join(' | ')} |`;
  }
  return lines.join('\n');
}

function setBridgeStatus(markdown: string, pathId: string | undefined, joinRowId: string, status: string) {
  let updated = pathId ? updateBridgeTableStatus(markdown, '跨场景分析路径', pathId, status) : markdown;
  updated = updateBridgeTableStatus(updated, '跨场景关联', joinRowId, status);
  return updated;
}

const buildSourceRefreshNotice = (
  revision: OntologyRevision,
  activeSources: OntologySourceRef[],
): OntologySourceRefresh | undefined => {
  const previous = new Map(revision.source_scene_snapshots.map(source => [source.scene_id, source]));
  const incoming = new Map(activeSources.map(source => [source.scene_id, source]));
  const sceneIds = Array.from(new Set([...previous.keys(), ...incoming.keys()])).sort();
  const changes: OntologySourceRefresh['changes'] = [];

  sceneIds.forEach(sceneId => {
    const before = previous.get(sceneId);
    const after = incoming.get(sceneId);
    if (!before && after) {
      changes.push({ scene_id: sceneId, kind: 'added', after });
      return;
    }
    if (before && !after) {
      changes.push({ scene_id: sceneId, kind: 'removed', before });
      return;
    }
    if (before && after && before.snapshot_id !== after.snapshot_id) {
      changes.push({ scene_id: sceneId, kind: 'changed', before, after });
    }
  });

  if (!changes.length) return undefined;
  return { pending_count: changes.length, changes, generation: null, revision };
};

const downloadMarkdown = (content: string, revision: number) => {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `global_business_v${revision}.md`;
  anchor.click();
  URL.revokeObjectURL(url);
};

export default function OntologyPage() {
  const [draft, setDraft] = useState<OntologyRevision>();
  const [active, setActive] = useState<OntologySnapshot | null>(null);
  const [markdown, setMarkdown] = useState('');
  const [graph, setGraph] = useState<OntologyGraph>();
  const [layout, setLayout] = useState<Record<string, unknown>>({});
  const [issues, setIssues] = useState<OntologyIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);
  const [mode, setMode] = useState<ViewMode>('split');
  const [sourceRefresh, setSourceRefresh] = useState<OntologySourceRefresh>();
  const [sourceScenes, setSourceScenes] = useState<OntologySourceRef[]>([]);
  const [lastGeneration, setLastGeneration] = useState<OntologySourceRefresh['generation']>();
  const [sourceRefreshOpen, setSourceRefreshOpen] = useState(false);
  const [diff, setDiff] = useState<{ original: string; modified: string }>();
  const [diffOpen, setDiffOpen] = useState(false);
  const [neo4jHealth, setNeo4jHealth] = useState<OntologyNeo4jHealth>();
  const [syncStatus, setSyncStatus] = useState<OntologySnapshotSyncStatus>();
  const [syncing, setSyncing] = useState(false);
  const [graphLayer, setGraphLayer] = useState<OntologyLayerView>('all');

  const changed = Boolean(draft && markdown !== draft.markdown);

  const loadGraph = useCallback(async (revision: number, replaceInvalid = false) => {
    const preview = await getOntologyGraph(revision);
    setIssues(preview.issues);
    setLayout(preview.layout);
    if (preview.valid || replaceInvalid) setGraph(preview.graph);
    return preview;
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getOntology();
      setDraft(data.draft);
      setActive(data.active_snapshot);
      setMarkdown(data.draft.markdown);
      setSourceScenes(data.source_scenes);
      setSourceRefresh(buildSourceRefreshNotice(data.draft, data.source_scenes));
      await loadGraph(data.draft.revision, true);
      try {
        const health = await getOntologyNeo4jHealth();
        setNeo4jHealth(health);
        if (data.active_snapshot) {
          setSyncStatus(await getOntologySnapshotSyncStatus(data.active_snapshot.snapshot_id));
        } else {
          setSyncStatus(undefined);
        }
      } catch {
        setNeo4jHealth({ enabled: true, status: 'unavailable' });
        setSyncStatus(undefined);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载 Ontology 失败');
    } finally {
      setLoading(false);
    }
  }, [loadGraph]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!draft || !changed) return;
    const timer = window.setTimeout(() => {
      void previewOntology(markdown)
        .then(result => {
          setIssues(result.issues);
          if (result.valid) setGraph(result.graph);
        })
        .catch(() => undefined);
    }, 700);
    return () => window.clearTimeout(timer);
  }, [changed, draft, markdown]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        if (changed && !saving) void save();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  });

  useEffect(() => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    });
  }, [mode]);

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const result = await saveOntologyDraft(markdown, draft.revision);
      setDraft(result);
      setMarkdown(result.markdown);
      setSourceRefresh(buildSourceRefreshNotice(result, sourceScenes));
      await loadGraph(result.revision);
      message.success('Ontology 草稿已保存');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const publish = async () => {
    if (!draft) return;
    if (changed) {
      message.warning('请先保存当前文档再发布');
      return;
    }
    setSaving(true);
    try {
      const validation = await validateOntologyRevision(draft.revision);
      setDraft(validation.revision);
      setIssues(validation.issues);
      if (!validation.valid || validation.issues.length > 0) {
        message.warning('请先处理发布检查提醒再发布');
        return;
      }
      await loadGraph(validation.revision.revision, true);
      const snapshot = await buildOntologySnapshot(validation.revision.revision);
      const activeSnapshot = await activateOntologySnapshot(snapshot.snapshot_id);
      setActive(activeSnapshot);
      message.success(`Ontology v${validation.revision.revision} 已发布`);
      await refresh();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '发布失败，请先处理格式提醒或场景语义文档版本变更');
    } finally {
      setSaving(false);
    }
  };

  const compileCheck = async () => {
    setChecking(true);
    try {
      const result =
        !changed && draft ? await validateOntologyRevision(draft.revision) : await previewOntology(markdown);
      setIssues(result.issues);
      if ('revision' in result) {
        setDraft(result.revision);
        setMarkdown(result.revision.markdown);
        await loadGraph(result.revision.revision, true);
      }
      if (result.valid) {
        if (!('revision' in result)) setGraph(result.graph);
        message.success(changed ? '格式检查通过，请保存后再发布' : '发布检查通过');
      } else {
        message.warning('格式检查完成，请查看提醒');
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '编译检查失败');
    } finally {
      setChecking(false);
    }
  };

  const syncActiveSnapshot = async () => {
    if (!active) return;
    setSyncing(true);
    try {
      const result = await syncOntologySnapshot(active.snapshot_id);
      setSyncStatus(result);
      message.success('当前本体快照已重新同步到 Neo4j');
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Neo4j 同步失败');
    } finally {
      setSyncing(false);
    }
  };

  const inspectSourceChanges = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const result = await generateOntologyFromScenes(draft.revision);
      setSourceRefresh(result);
      setLastGeneration(undefined);
      setSourceRefreshOpen(true);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '获取场景语义文档版本变更失败');
    } finally {
      setSaving(false);
    }
  };

  const applySourceChanges = async () => {
    if (!draft || !sourceRefresh) return;
    setSaving(true);
    try {
      const result = await generateOntologyFromScenes(draft.revision, true);
      setDraft(result.revision);
      setMarkdown(result.revision.markdown);
      setSourceRefresh(undefined);
      setLastGeneration(result.generation);
      setSourceRefreshOpen(false);
      await loadGraph(result.revision.revision);
      const summary = result.generation
        ? `实体 ${result.generation.entity_count} 个，指标 ${result.generation.metric_count} 个，分析规则 ${result.generation.analysis_rule_count} 条`
        : '已根据场景语义文档生成本体草稿';
      message.success(`已生成本体草稿：${summary}`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '生成本体草稿失败');
    } finally {
      setSaving(false);
    }
  };

  const showDiff = async () => {
    if (!draft || !active) {
      message.info('当前没有可比较的线上版本');
      return;
    }
    setSaving(true);
    try {
      const revisions = await listOntologyRevisions();
      const online = revisions.items.find(item => item.revision === active.revision);
      if (!online) throw new Error('未找到线上版本文档');
      setDiff({ original: online.markdown, modified: markdown });
      setDiffOpen(true);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载版本 Diff 失败');
    } finally {
      setSaving(false);
    }
  };

  const importMarkdown: UploadProps['beforeUpload'] = async file => {
    if (!file.name.toLowerCase().endsWith('.md')) {
      message.error('仅支持上传 .md 格式的本体文档');
      return Upload.LIST_IGNORE;
    }
    setMarkdown(await file.text());
    message.success(`已导入 ${file.name}`);
    return Upload.LIST_IGNORE;
  };

  const reviewBridge = (
    pathId: string | undefined,
    joinRowId: string,
    status: 'confirmed' | 'rejected' | 'candidate',
  ) => {
    setMarkdown(current => setBridgeStatus(current, pathId, joinRowId, status));
    message.success('状态已写入本体草稿；保存并发布后生效');
  };

  if (loading || !draft) return <Spin className='flex h-full items-center justify-center' />;

  const showDocument = mode !== 'graph';
  const showGraph = mode !== 'document';
  const canApplySourceRefresh = Boolean(sourceRefresh);
  const sourceBySceneId = new Map(sourceScenes.map(source => [source.scene_id, source]));
  const layerForNode = (type: string): OntologyLayer =>
    ['scene', 'scene_snapshot', 'result_binding', 'evidence', 'review_record', 'source_document'].includes(type)
      ? 'mapping_governance'
      : [
            'rule',
            'analysis_dimension',
            'analysis_path',
            'analysis_rule',
            'clarification_rule',
            'alignment_condition',
          ].includes(type)
        ? 'analysis_explanations'
        : 'business_semantics';
  const layerForEdge = (type: string): OntologyLayer =>
    [
      'scene_binding',
      'provided_by',
      'has_snapshot',
      'declares_binding',
      'maps_to',
      'supported_by',
      'from_document',
      'reviews',
    ].includes(type)
      ? 'mapping_governance'
      : [
            'cross_scene_join',
            'metric_relation',
            'clarification_rule',
            'applies_to_metric',
            'applies_to_entity',
            'from_metric',
            'to_metric',
            'requires',
          ].includes(type)
        ? 'analysis_explanations'
        : 'business_semantics';
  const layerCounts = (graph?.nodes ?? []).reduce<Record<OntologyLayer, number>>(
    (counts, node) => {
      counts[layerForNode(node.type)] += 1;
      return counts;
    },
    { business_semantics: 0, analysis_explanations: 0, mapping_governance: 0 },
  );
  const visibleGraph =
    !graph || graphLayer === 'all'
      ? graph
      : (() => {
          const edges = graph.edges.filter(edge => layerForEdge(edge.type) === graphLayer);
          const includedIds = new Set(edges.flatMap(edge => [edge.source, edge.target]));
          const nodes = graph.nodes.filter(node => layerForNode(node.type) === graphLayer || includedIds.has(node.id));
          return { nodes, edges };
        })();
  const publishStatusIcon = active ? (
    <Tooltip title={`已发布 v${active.revision}`}>
      <CheckCircleFilled className='text-[15px] text-emerald-500' />
    </Tooltip>
  ) : (
    <Tooltip title='尚未发布'>
      <ExclamationCircleFilled className='text-[15px] text-gray-400' />
    </Tooltip>
  );
  const pathCandidates: BridgeReviewItem[] = (graph?.nodes ?? [])
    .filter(node => node.type === 'analysis_path')
    .map(node => ({ pathId: node.id, joinRowId: `${node.id}_join`, data: node.data ?? {}, name: node.name }));
  const pathJoinIds = new Set(pathCandidates.map(item => item.joinRowId));
  const edgeCandidates = (graph?.edges ?? [])
    .filter(edge => edge.type === 'cross_scene_join' && !pathJoinIds.has(edge.id))
    .map(edge => {
      const data = edge.data ?? {};
      const split = (value: unknown) =>
        String(value ?? '')
          .split(/[,，、;；]+/)
          .map(item => item.trim())
          .filter(Boolean);
      return {
        pathId: undefined,
        joinRowId: edge.id,
        name: `${edge.source} ↔ ${edge.target}`,
        data: {
          ...data,
          scenes: Array.isArray(data.scenes) ? data.scenes : [edge.source, edge.target],
          join_keys: split(data.business_key),
          grain_alignment: Array.isArray(data.grain_alignment) ? data.grain_alignment : split(data.grain),
        } as Record<string, unknown>,
      };
    }) as BridgeReviewItem[];
  const bridgeCandidates = [...pathCandidates, ...edgeCandidates];

  return (
    <AskDataPageShell
      title={
        <span className='inline-flex items-center gap-2'>
          Ontology
          {publishStatusIcon}
        </span>
      }
      description='全局业务本体'
      maxWidth='max-w-7xl'
      actions={
        <>
          <Tooltip title='重新加载草稿和线上版本'>
            <Button icon={<ReloadOutlined />} onClick={() => void refresh()} disabled={saving} />
          </Tooltip>
          <Tooltip title='查看草稿与线上版本的差异'>
            <Button icon={<DiffOutlined />} onClick={() => void showDiff()} disabled={saving || !active} />
          </Tooltip>
          <Tooltip title='直接读取可访问的已激活场景语义文档，生成全局本体草稿；不会写回场景'>
            <Button icon={<BranchesOutlined />} onClick={() => void inspectSourceChanges()} loading={saving}>
              生成草稿
            </Button>
          </Tooltip>
          <Button
            icon={<FileSearchOutlined />}
            onClick={() => void compileCheck()}
            loading={checking}
            disabled={saving}
          >
            编译检查
          </Button>
          <Button icon={<SaveOutlined />} onClick={() => void save()} loading={saving} disabled={!changed}>
            保存
          </Button>
          <Button type='primary' icon={<CloudUploadOutlined />} onClick={() => void publish()} loading={saving}>
            发布
          </Button>
        </>
      }
    >
      <section className='overflow-hidden rounded-md border border-gray-200/80 bg-white shadow-sm dark:border-white/10 dark:bg-[#1a1b1e]'>
        <div className='flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-white/10'>
          <div className='flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300'>
            <ApartmentOutlined className='text-blue-600' />
            <span>global_business</span>
          </div>
          <div className='flex items-center gap-2'>
            <Tooltip title='下载当前 Markdown 文档'>
              <Button
                size='small'
                icon={<DownloadOutlined />}
                onClick={() => downloadMarkdown(markdown, draft.revision)}
              />
            </Tooltip>
            <Upload accept='.md' showUploadList={false} beforeUpload={importMarkdown}>
              <Button size='small' icon={<UploadOutlined />}>
                导入
              </Button>
            </Upload>
            <Segmented<ViewMode>
              size='small'
              value={mode}
              onChange={value => setMode(value)}
              options={[
                { value: 'split', label: '分屏' },
                { value: 'document', label: '文档' },
                { value: 'graph', label: '图谱' },
              ]}
            />
          </div>
        </div>
        {issues.length > 0 && (
          <Alert className='m-4 mb-0' type='warning' showIcon message='编译检查提醒' description={issueText(issues)} />
        )}
        {sourceRefresh?.pending_count ? (
          <Alert
            className='m-4 mb-0'
            type='info'
            showIcon
            message={`检测到 ${sourceRefresh.pending_count} 个已激活场景版本变更`}
            description='已有新的 Scene Snapshot，建议重新生成全局本体草稿，审核后再发布。'
            action={
              <Button size='small' type='primary' onClick={() => void inspectSourceChanges()} loading={saving}>
                查看变更
              </Button>
            }
          />
        ) : null}
        {lastGeneration && (
          <Alert
            className='m-4 mb-0'
            type='success'
            showIcon
            message='最近一次生成摘要'
            description={`模式：${lastGeneration.mode.toUpperCase()}；来源场景 ${lastGeneration.source_count} 个；实体 ${lastGeneration.entity_count} 个；指标 ${lastGeneration.metric_count} 个；关系 ${lastGeneration.relation_count} 个；分析规则 ${lastGeneration.analysis_rule_count} 条`}
          />
        )}
        <div className='mx-4 mt-4 grid gap-3 md:grid-cols-2'>
          <div className='rounded-md border border-gray-200 p-4 dark:border-white/10'>
            <div className='flex items-center justify-between'>
              <div className='flex items-center gap-2 font-medium'>
                <DatabaseOutlined className='text-blue-600' /> Neo4j 发布存储
              </div>
              <Tag
                color={
                  neo4jHealth?.status === 'ready' ? 'green' : neo4jHealth?.status === 'disabled' ? 'default' : 'orange'
                }
              >
                {neo4jHealth?.status === 'ready'
                  ? '已连接'
                  : neo4jHealth?.status === 'disabled'
                    ? '未启用'
                    : neo4jHealth?.status === 'unavailable'
                      ? '不可用'
                      : '待检查'}
              </Tag>
            </div>
            <div className='mt-2 text-xs text-gray-500'>
              {syncStatus
                ? `快照 ${syncStatus.snapshot_id} · ${syncStatus.node_count ?? 0} 个对象 · ${syncStatus.relationship_count ?? 0} 条关系 · ${syncStatus.status}`
                : '发布后可查看当前快照的图存储同步状态。'}
            </div>
            {syncStatus?.sync_error && <div className='mt-2 text-xs text-red-600'>{syncStatus.sync_error}</div>}
            {active && neo4jHealth?.status === 'ready' && (
              <Button
                className='mt-3'
                size='small'
                icon={<SyncOutlined />}
                loading={syncing}
                onClick={() => void syncActiveSnapshot()}
              >
                重试当前快照同步
              </Button>
            )}
          </div>
          <div className='rounded-md border border-gray-200 p-4 dark:border-white/10'>
            <div className='font-medium'>{syncStatus?.layer_counts ? '已发布快照三层对象' : '草稿三层节点'}</div>
            <div className='mt-3 flex flex-wrap gap-2 text-xs'>
              {(
                [
                  ['business_semantics', '业务语义', 'blue'],
                  ['analysis_explanations', '分析解释', 'purple'],
                  ['mapping_governance', '映射治理', 'cyan'],
                ] as const
              ).map(([layer, label, color]) => {
                const publishedCount = syncStatus?.layer_counts?.[layer];
                return (
                  <Tag key={layer} color={color}>
                    {label}{' '}
                    {publishedCount
                      ? `${publishedCount.nodes} 节点 / ${publishedCount.relationships} 关系`
                      : `${layerCounts[layer]} 节点`}
                  </Tag>
                );
              })}
            </div>
            <div className='mt-2 text-xs text-gray-500'>
              三层在发布时编译为带 layer 属性的图对象，当前分析召回仍使用 JSON 快照。
            </div>
          </div>
        </div>
        <div className='mx-4 mt-4 rounded-md border border-gray-200 p-4 dark:border-white/10'>
          <div className='mb-3 flex items-center justify-between'>
            <div>
              <div className='font-medium'>跨场景分析桥接</div>
              <div className='mt-1 text-xs text-gray-500'>
                候选桥接只在本体页审核；只有状态为“已确认”且分析契约完整的桥接会进入查数后分析。
              </div>
            </div>
            <Tag color={bridgeCandidates.some(item => item.data?.status === 'confirmed') ? 'green' : 'default'}>
              {bridgeCandidates.filter(item => item.data?.status === 'confirmed').length} 已确认 /{' '}
              {bridgeCandidates.length} 条
            </Tag>
          </div>
          {bridgeCandidates.length ? (
            <div className='space-y-3'>
              {bridgeCandidates.map(bridge => {
                const data = bridge.data ?? {};
                const status = String(data.status ?? 'candidate');
                const scenes = Array.isArray(data.scenes) ? data.scenes.map(String) : [];
                const strings = (value: unknown) => (Array.isArray(value) ? value.map(String) : []);
                const canConfirm = Boolean(
                  scenes.length === 2 &&
                    new Set(scenes).size === 2 &&
                    strings(data.join_keys).length &&
                    data.relation_type &&
                    data.mapping_source &&
                    data.left_time_basis &&
                    data.right_time_basis &&
                    strings(data.time_alignment_conditions).length &&
                    strings(data.grain_alignment).length &&
                    strings(data.allowed_analyses).length &&
                    strings(data.forbidden_inferences).length &&
                    data.evidence,
                );
                return (
                  <div key={bridge.joinRowId} className='rounded border border-gray-100 p-3 dark:border-white/10'>
                    <div className='flex flex-wrap items-center justify-between gap-2'>
                      <div className='flex items-center gap-2'>
                        <span className='font-medium'>{bridge.name}</span>
                        <Tag color={status === 'confirmed' ? 'green' : status === 'rejected' ? 'red' : 'gold'}>
                          {status === 'confirmed' ? '已确认' : status === 'rejected' ? '已驳回' : '待审核'}
                        </Tag>
                        {data.relation_type ? <Tag>{String(data.relation_type)}</Tag> : null}
                      </div>
                      <div className='flex gap-2'>
                        {status !== 'confirmed' && (
                          <Tooltip
                            title={
                              canConfirm
                                ? '写入已确认状态，保存并发布后进入默认分析'
                                : '请补齐关系类型、关联键、映射依据、时间/粒度条件、允许/禁止分析和证据'
                            }
                          >
                            <span>
                              <Button
                                size='small'
                                type='primary'
                                disabled={!canConfirm}
                                onClick={() => reviewBridge(bridge.pathId, bridge.joinRowId, 'confirmed')}
                              >
                                确认桥接
                              </Button>
                            </span>
                          </Tooltip>
                        )}
                        {status === 'confirmed' ? (
                          <Button
                            size='small'
                            onClick={() => reviewBridge(bridge.pathId, bridge.joinRowId, 'candidate')}
                          >
                            撤回确认
                          </Button>
                        ) : status !== 'rejected' ? (
                          <Button
                            size='small'
                            danger
                            onClick={() => reviewBridge(bridge.pathId, bridge.joinRowId, 'rejected')}
                          >
                            驳回
                          </Button>
                        ) : null}
                      </div>
                    </div>
                    <div className='mt-2 text-sm text-gray-600 dark:text-gray-300'>
                      涉及场景：{scenes.join('、') || '未填写'}；关联键：
                      {strings(data.join_keys).join('、') || '未填写'}
                    </div>
                    <div className='mt-1 text-xs text-gray-500'>
                      映射依据：{String(data.mapping_source || '未填写')}；时间：
                      {String(data.left_time_basis || '未填写')} → {String(data.right_time_basis || '未填写')}
                      ；粒度条件：{strings(data.grain_alignment).join('、') || '未填写'}
                    </div>
                    <div className='mt-1 text-xs text-gray-500'>
                      时间对齐：{strings(data.time_alignment_conditions).join('、') || '未填写'}；允许分析：
                      {strings(data.allowed_analyses).join('、') || '未填写'}
                    </div>
                    {strings(data.forbidden_inferences).length > 0 && (
                      <div className='mt-1 text-xs text-amber-700'>
                        禁止推断：{strings(data.forbidden_inferences).join('、')}
                      </div>
                    )}
                    {data.evidence ? (
                      <div className='mt-1 text-xs text-gray-500'>证据：{String(data.evidence)}</div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className='text-sm text-gray-500'>当前草稿没有跨场景分析路径。</div>
          )}
        </div>
        <div className={`grid min-h-[680px] ${showDocument && showGraph ? 'lg:grid-cols-2' : 'grid-cols-1'}`}>
          <div
            className={`${showDocument ? 'flex' : 'hidden'} ${showGraph ? 'border-b lg:border-r lg:border-b-0' : ''} min-h-[680px] flex-col border-gray-100 dark:border-white/10`}
          >
            <div className='flex items-center gap-2 border-b border-gray-100 px-4 py-3 dark:border-white/10'>
              <ApartmentOutlined className='text-blue-600' />
              <span className='text-sm font-medium'>本体 Markdown</span>
              {changed && <span className='text-xs text-amber-600'>未保存修改</span>}
            </div>
            <div className='min-h-0 flex-1'>
              <MonacoEditor
                className='h-[632px]'
                value={markdown}
                language='markdown'
                onChange={value => setMarkdown(value ?? '')}
              />
            </div>
          </div>
          <div className={`${showGraph ? 'flex' : 'hidden'} min-h-[680px] flex-col`}>
            <div className='flex items-center justify-between border-b border-gray-100 px-4 py-3 dark:border-white/10'>
              <div className='flex items-center gap-2'>
                <ApartmentOutlined className='text-blue-600' />
                <span className='text-sm font-medium'>本体图谱</span>
              </div>
              <div className='flex items-center gap-3'>
                <Segmented<OntologyLayerView>
                  size='small'
                  value={graphLayer}
                  onChange={setGraphLayer}
                  options={[
                    { value: 'all', label: '全部' },
                    { value: 'business_semantics', label: `业务语义 ${layerCounts.business_semantics}` },
                    { value: 'analysis_explanations', label: `分析解释 ${layerCounts.analysis_explanations}` },
                    { value: 'mapping_governance', label: `映射治理 ${layerCounts.mapping_governance}` },
                  ]}
                />
                <span className='text-xs text-gray-500'>布局独立于业务版本</span>
              </div>
            </div>
            <div className='min-h-0 flex-1 p-3'>
              <div className='h-[632px] rounded-md bg-slate-50 dark:bg-[#111217]'>
                <OntologyGraphCanvas
                  graph={visibleGraph}
                  layout={layout}
                  onSaveLayout={async value => {
                    await saveOntologyLayout(draft.revision, value);
                    setLayout(value);
                    message.success('节点布局已保存');
                  }}
                />
              </div>
            </div>
          </div>
        </div>
      </section>
      <Modal
        title='从场景语义文档生成本体草稿'
        open={sourceRefreshOpen}
        onCancel={() => setSourceRefreshOpen(false)}
        onOk={() => void applySourceChanges()}
        okText={sourceRefresh?.pending_count ? '生成草稿' : '重新生成草稿'}
        okButtonProps={{ loading: saving, disabled: !canApplySourceRefresh || saving }}
        cancelText='关闭'
      >
        <div className='space-y-3'>
          <p className='m-0 text-sm text-gray-600 dark:text-gray-300'>
            将使用 LLM 读取已激活场景语义文档，生成面向查数后分析的全局业务本体草稿。不会写回场景。
          </p>
          {saving && (
            <Alert
              type='info'
              showIcon
              message='正在生成'
              description='读取场景语义文档 → 抽取单场景业务语义 → 归并全局业务本体 → 生成 Markdown → 刷新图谱'
            />
          )}
          {sourceScenes.length > 0 && (
            <div className='space-y-2'>
              <div className='text-xs font-medium text-gray-500'>已激活来源</div>
              {sourceScenes.map(source => {
                const label = sourceName(source);
                return (
                  <div
                    key={`${source.scene_id}-${source.snapshot_id}`}
                    className='rounded-md border border-gray-200 px-3 py-2 text-xs dark:border-white/10'
                  >
                    <div className='font-medium text-gray-700 dark:text-gray-200'>{label}</div>
                    {label !== source.scene_id && <div className='mt-1 text-gray-500'>ID：{source.scene_id}</div>}
                    <div className='mt-1 text-gray-500'>
                      revision {source.revision_id || '-'} · {source.semantic_md_hash?.slice(0, 18) || '-'}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {sourceRefresh?.pending_count ? (
            <div className='space-y-2'>
              <div className='text-xs font-medium text-gray-500'>版本变更</div>
              {sourceRefresh.changes.map(change => {
                const source = sourceBySceneId.get(change.scene_id) ?? change.after ?? change.before ?? change;
                const label = sourceName(source);
                return (
                  <div
                    key={change.scene_id}
                    className='rounded-md border border-gray-200 px-3 py-2 text-sm dark:border-white/10'
                  >
                    <strong>{label}</strong>
                    <span className='ml-2 text-gray-500'>
                      {change.kind === 'added' ? '新增' : change.kind === 'removed' ? '移除' : '已更新'}
                    </span>
                    {label !== change.scene_id && (
                      <div className='mt-1 text-xs text-gray-500'>ID：{change.scene_id}</div>
                    )}
                    {change.before?.snapshot_id && (
                      <div className='mt-1 text-xs text-gray-500'>原版本：{change.before.snapshot_id}</div>
                    )}
                    {change.after?.snapshot_id && (
                      <div className='mt-1 text-xs text-gray-500'>当前版本：{change.after.snapshot_id}</div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className='m-0 text-sm text-gray-600 dark:text-gray-300'>
              当前草稿已记录所有可访问的已激活场景语义文档版本；如需刷新内容，可以继续重新生成草稿。
            </p>
          )}
        </div>
      </Modal>
      <Modal title='草稿与线上版本 Diff' open={diffOpen} onCancel={() => setDiffOpen(false)} footer={null} width={1000}>
        {diff && (
          <DiffEditor
            height='65vh'
            language='markdown'
            original={diff.original}
            modified={diff.modified}
            options={{ readOnly: true, minimap: { enabled: false }, renderSideBySide: true }}
          />
        )}
      </Modal>
    </AskDataPageShell>
  );
}
