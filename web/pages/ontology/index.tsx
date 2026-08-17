import AskDataPageShell from '@/components/ask-data/AskDataPageShell';
import {
  activateOntologySnapshot,
  buildOntologySnapshot,
  getOntology,
  getOntologyGraph,
  listOntologyRevisions,
  previewOntology,
  saveOntologyDraft,
  saveOntologyLayout,
  syncOntologyScenes,
  validateOntologyRevision,
  type OntologyGraph,
  type OntologyIssue,
  type OntologyRevision,
  type OntologySceneSync,
  type OntologySnapshot,
} from '@/utils/ontology';
import {
  ApartmentOutlined,
  CloudUploadOutlined,
  CodeOutlined,
  DiffOutlined,
  DownloadOutlined,
  ReloadOutlined,
  SaveOutlined,
  SyncOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import type { UploadProps } from 'antd';
import { Alert, Button, Modal, Segmented, Spin, Tag, Tooltip, Upload, message } from 'antd';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useState } from 'react';

const MonacoEditor = dynamic(() => import('@/components/chat/monaco-editor'), { ssr: false });
const OntologyGraphCanvas = dynamic(() => import('@/components/ontology/OntologyGraphCanvas'), { ssr: false });
const DiffEditor = dynamic(() => import('@monaco-editor/react').then(module => module.DiffEditor), { ssr: false });

type ViewMode = 'split' | 'document' | 'graph';

const issueText = (issues: OntologyIssue[]) =>
  issues
    .slice(0, 3)
    .map(issue => `${issue.line ? `第 ${issue.line} 行：` : ''}${issue.message}`)
    .join('；');

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
  const [mode, setMode] = useState<ViewMode>('split');
  const [sync, setSync] = useState<OntologySceneSync>();
  const [syncOpen, setSyncOpen] = useState(false);
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

  const validate = async () => {
    if (!draft) return;
    if (changed) {
      message.warning('请先保存当前文档再校验');
      return;
    }
    setSaving(true);
    try {
      const result = await validateOntologyRevision(draft.revision);
      setDraft(result.revision);
      setIssues(result.issues);
      if (result.valid) {
        await loadGraph(result.revision.revision);
        message.success('本体文档校验通过');
      } else message.warning('本体文档存在需要处理的问题');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '校验失败');
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
      const snapshot = await buildOntologySnapshot(draft.revision);
      const activeSnapshot = await activateOntologySnapshot(snapshot.snapshot_id);
      setActive(activeSnapshot);
      message.success(`Ontology v${draft.revision} 已发布`);
      await refresh();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '发布失败，请先处理校验或场景同步问题');
    } finally {
      setSaving(false);
    }
  };

  const inspectSceneSync = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const result = await syncOntologyScenes(draft.revision);
      setSync(result);
      setSyncOpen(true);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '获取场景同步信息失败');
    } finally {
      setSaving(false);
    }
  };

  const applySceneSync = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const result = await syncOntologyScenes(draft.revision, true);
      setDraft(result.revision);
      setMarkdown(result.revision.markdown);
      setSync(result);
      setSyncOpen(false);
      await loadGraph(result.revision.revision);
      message.success('场景变更已同步到 Ontology 草稿');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '同步场景失败');
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

  const status = useMemo(() => {
    if (!draft) return null;
    return (
      <div className='flex items-center gap-2'>
        <Tag color={changed ? 'warning' : 'default'}>
          草稿 v{draft.revision}
          {changed ? '，未保存' : ''}
        </Tag>
        <Tag color={active ? 'success' : 'default'}>{active ? `线上 v${active.revision}` : '尚未发布'}</Tag>
      </div>
    );
  }, [active, changed, draft]);

  if (loading || !draft) return <Spin className='flex h-full items-center justify-center' />;

  const showDocument = mode !== 'graph';
  const showGraph = mode !== 'document';

  return (
    <AskDataPageShell
      title='Ontology'
      description='全局业务本体'
      maxWidth='max-w-7xl'
      actions={
        <>
          {status}
          <Tooltip title='重新加载草稿和线上版本'>
            <Button icon={<ReloadOutlined />} onClick={() => void refresh()} disabled={saving} />
          </Tooltip>
          <Tooltip title='查看草稿与线上版本的差异'>
            <Button icon={<DiffOutlined />} onClick={() => void showDiff()} disabled={saving || !active} />
          </Tooltip>
          <Button icon={<SyncOutlined />} onClick={() => void inspectSceneSync()} loading={saving}>
            同步场景
          </Button>
          <Button icon={<CodeOutlined />} onClick={() => void validate()} loading={saving}>
            校验
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
          <Alert
            className='m-4 mb-0'
            type='warning'
            showIcon
            message='本体文档尚未通过校验'
            description={issueText(issues)}
          />
        )}
        <div className={`grid min-h-[680px] ${showDocument && showGraph ? 'lg:grid-cols-2' : 'grid-cols-1'}`}>
          {showDocument && (
            <div
              className={`${showGraph ? 'border-b lg:border-r lg:border-b-0' : ''} flex min-h-[680px] flex-col border-gray-100 dark:border-white/10`}
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
          )}
          {showGraph && (
            <div className='flex min-h-[680px] flex-col'>
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
          )}
        </div>
      </section>
      <Modal
        title='场景同步'
        open={syncOpen}
        onCancel={() => setSyncOpen(false)}
        onOk={() => void applySceneSync()}
        okText='接受变更'
        okButtonProps={{ disabled: !sync?.pending_count, loading: saving }}
        cancelText='关闭'
      >
        {sync?.pending_count ? (
          <div className='space-y-3'>
            <p className='m-0 text-sm text-gray-600 dark:text-gray-300'>检测到 {sync.pending_count} 个场景版本变更。</p>
            {sync.changes.map(change => (
              <div
                key={change.scene_id}
                className='rounded-md border border-gray-200 px-3 py-2 text-sm dark:border-white/10'
              >
                <strong>{change.scene_id}</strong>
                <span className='ml-2 text-gray-500'>
                  {change.kind === 'added' ? '新增' : change.kind === 'removed' ? '移除' : '已更新'}
                </span>
                {change.before?.snapshot_id && (
                  <div className='mt-1 text-xs text-gray-500'>原版本：{change.before.snapshot_id}</div>
                )}
                {change.after?.snapshot_id && (
                  <div className='mt-1 text-xs text-gray-500'>当前版本：{change.after.snapshot_id}</div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className='m-0 text-sm text-gray-600 dark:text-gray-300'>所有已发布场景均已同步。</p>
        )}
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
