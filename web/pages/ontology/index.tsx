import AskDataPageShell from '@/components/ask-data/AskDataPageShell';
import {
  activateOntologySnapshot,
  buildOntologySnapshot,
  generateOntologyFromScenes,
  getOntology,
  getOntologyGraph,
  listOntologyRevisions,
  previewOntology,
  saveOntologyDraft,
  saveOntologyLayout,
  type OntologyGraph,
  type OntologyIssue,
  type OntologyRevision,
  type OntologySnapshot,
  type OntologySourceRef,
  type OntologySourceRefresh,
} from '@/utils/ontology';
import {
  ApartmentOutlined,
  BranchesOutlined,
  CheckCircleFilled,
  CloudUploadOutlined,
  DiffOutlined,
  DownloadOutlined,
  ExclamationCircleFilled,
  FileSearchOutlined,
  ReloadOutlined,
  SaveOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { Alert, Button, Modal, Segmented, Spin, Tooltip, Upload, message } from 'antd';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useState } from 'react';

const MonacoEditor = dynamic(() => import('@/components/chat/monaco-editor'), { ssr: false });
const OntologyGraphCanvas = dynamic(() => import('@/components/ontology/OntologyGraphCanvas'), { ssr: false });
const DiffEditor = dynamic(() => import('@monaco-editor/react').then(module => module.DiffEditor), { ssr: false });

type ViewMode = 'split' | 'document' | 'graph';

const issueText = (issues: OntologyIssue[]) =>
  issues
    .slice(0, 3)
    .map(issue => `${issue.line ? `第 ${issue.line} 行：` : ''}${issue.message}`)
    .join('；');

const sourceName = (source?: Partial<OntologySourceRef>) => source?.scene_name || source?.scene_id || '-';

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
      await loadGraph(data.draft.revision, true);
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
      const preview = await previewOntology(markdown);
      setIssues(preview.issues);
      if (!preview.valid || preview.issues.length > 0) {
        message.warning('请先处理编译检查提醒再发布');
        return;
      }
      setGraph(preview.graph);
      const snapshot = await buildOntologySnapshot(draft.revision);
      const activeSnapshot = await activateOntologySnapshot(snapshot.snapshot_id);
      setActive(activeSnapshot);
      message.success(`Ontology v${draft.revision} 已发布`);
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
      const result = await previewOntology(markdown);
      setIssues(result.issues);
      if (result.valid) {
        setGraph(result.graph);
        message.success('格式检查通过');
      } else {
        message.warning('格式检查完成，请查看提醒');
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '编译检查失败');
    } finally {
      setChecking(false);
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
      setSourceRefresh(result);
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

  if (loading || !draft) return <Spin className='flex h-full items-center justify-center' />;

  const showDocument = mode !== 'graph';
  const showGraph = mode !== 'document';
  const canApplySourceRefresh = Boolean(sourceRefresh);
  const sourceBySceneId = new Map(sourceScenes.map(source => [source.scene_id, source]));
  const publishStatusIcon = active ? (
    <Tooltip title={`已发布 v${active.revision}`}>
      <CheckCircleFilled className='text-[15px] text-emerald-500' />
    </Tooltip>
  ) : (
    <Tooltip title='尚未发布'>
      <ExclamationCircleFilled className='text-[15px] text-gray-400' />
    </Tooltip>
  );

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
        {lastGeneration && (
          <Alert
            className='m-4 mb-0'
            type='success'
            showIcon
            message='最近一次生成摘要'
            description={`模式：${lastGeneration.mode.toUpperCase()}；来源场景 ${lastGeneration.source_count} 个；实体 ${lastGeneration.entity_count} 个；指标 ${lastGeneration.metric_count} 个；关系 ${lastGeneration.relation_count} 个；分析规则 ${lastGeneration.analysis_rule_count} 条`}
          />
        )}
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
              <span className='text-xs text-gray-500'>布局独立于业务版本</span>
            </div>
            <div className='min-h-0 flex-1 p-3'>
              <div className='h-[632px] rounded-md bg-slate-50 dark:bg-[#111217]'>
                <OntologyGraphCanvas
                  graph={graph}
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
