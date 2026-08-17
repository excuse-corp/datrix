import { ResultDisplay } from '@/components/ask-data/ResultDisplay';
import {
  changeSceneStatus,
  deleteScene,
  getApiSpec,
  getScene,
  getSemanticMarkdown,
  submitSceneQuery,
  validateRevision,
  type ApiSpec,
  type AskDataQueryResponse,
  type SceneSummary,
} from '@/utils/ask-data';
import {
  defaultQueryLimits,
  parseSemanticMarkdown,
  queryLimitDefinitions,
  type QueryLimits,
} from '@/utils/ask-data-semantic';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Input,
  Popconfirm,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import { useRouter } from 'next/router';
import { useCallback, useEffect, useState } from 'react';

const errorMessage = (error: unknown) => (error instanceof Error ? error.message : '操作失败');

const formatLimitValue = (key: keyof QueryLimits, value: unknown) => {
  if (key === 'allow_detail') return value === true ? '允许' : '关闭';
  const definition = queryLimitDefinitions.find(item => item.key === key);
  const numeric = Number(value ?? defaultQueryLimits[key]);
  return `${Number.isFinite(numeric) ? numeric.toLocaleString() : '-'}${definition?.unit ? ` ${definition.unit}` : ''}`;
};

const documentCard = (title: string, content?: string) => (
  <Card title={title} className='h-full'>
    {content?.trim() ? (
      <pre className='max-h-[36rem] overflow-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-4 text-[13px] leading-6 dark:bg-[#0f1012]'>
        {content.trim()}
      </pre>
    ) : (
      <Alert type='info' message='暂无文档内容' />
    )}
  </Card>
);

export default function SceneDetailPage() {
  const router = useRouter();
  const sceneId = typeof router.query.scene_id === 'string' ? router.query.scene_id : '';
  const [scene, setScene] = useState<SceneSummary | null>(null);
  const [documents, setDocuments] = useState(parseSemanticMarkdown(''));
  const [apiSpec, setApiSpec] = useState<ApiSpec | null>(null);
  const [question, setQuestion] = useState('');
  const [result, setResult] = useState<AskDataQueryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const reload = useCallback(async () => {
    if (!sceneId) return;
    setLoading(true);
    try {
      const [scenePayload, markdownPayload, specPayload] = await Promise.all([
        getScene(sceneId),
        getSemanticMarkdown(sceneId),
        getApiSpec(sceneId).catch(() => null),
      ]);
      setScene(scenePayload.data ?? null);
      setDocuments(parseSemanticMarkdown(markdownPayload.data?.semantic_md ?? ''));
      setApiSpec(specPayload?.data ?? null);
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [sceneId]);
  useEffect(() => {
    void reload();
  }, [reload]);
  const execute = async (action: () => Promise<unknown>, success: string) => {
    try {
      await action();
      message.success(success);
      await reload();
    } catch (error) {
      message.error(errorMessage(error));
    }
  };
  const runSceneTest = async () => {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      message.warning('请输入测试问题');
      return;
    }
    setTesting(true);
    setResult(null);
    try {
      const response = await submitSceneQuery(sceneId, {
        question: trimmedQuestion,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      });
      setResult(response);
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setTesting(false);
    }
  };
  if (loading && !scene)
    return (
      <main className='min-h-0 flex-1 overflow-y-auto p-6'>
        <Spin />
      </main>
    );
  if (!scene)
    return (
      <main className='min-h-0 flex-1 overflow-y-auto p-6'>
        <Alert type='error' message='未找到场景或没有访问权限' />
      </main>
    );
  const revision = scene.latest_revision;
  const limits =
    scene.limits && Object.keys(scene.limits).length
      ? (scene.limits as Partial<Record<keyof QueryLimits, unknown>>)
      : (documents.query_limits ?? defaultQueryLimits);
  return (
    <main className='ask-data-page-scroll h-full min-h-0 flex-1 overflow-y-auto bg-[#f7f9fc] pb-24 dark:bg-[#111827]'>
      <div className='mx-auto max-w-6xl p-4 pb-16 md:p-6'>
        <div className='mb-4 flex flex-wrap items-start justify-between gap-3'>
          <div>
            <Typography.Title level={2} className='!mb-1'>
              {scene.name}
            </Typography.Title>
            <Typography.Text type='secondary'>{scene.description}</Typography.Text>
          </div>
          <Space wrap>
            <Button
              disabled={scene.status !== 'inactive'}
              href={
                scene.status === 'inactive'
                  ? `/ask-data/scenes/edit?scene_id=${encodeURIComponent(sceneId)}`
                  : undefined
              }
            >
              编辑
            </Button>
            <Button onClick={() => void execute(() => validateRevision(sceneId, revision), '校验通过，场景已启用')}>
              校验
            </Button>
            <Button
              onClick={() =>
                void execute(
                  () => changeSceneStatus(sceneId, scene.status === 'active' ? 'disable' : 'enable'),
                  scene.status === 'active' ? '场景已停用' : '场景已启用',
                )
              }
            >
              {scene.status === 'active' ? '停用' : '启用'}
            </Button>
            <Popconfirm
              title='确认软删除？历史运行记录仍会保留。'
              onConfirm={() =>
                void execute(() => deleteScene(sceneId), '场景已删除').then(() => router.push('/ask-data/scenes'))
              }
            >
              <Button danger>删除</Button>
            </Popconfirm>
          </Space>
        </div>
        {scene.status === 'invalid' && (
          <Alert className='mb-4' type='error' showIcon message='场景无效或发生 Schema 漂移，请先修复并重新发布。' />
        )}
        <Descriptions
          bordered
          size='small'
          column={{ xs: 1, md: 2 }}
          items={[
            { key: 'status', label: '状态', children: <Tag>{scene.status}</Tag> },
            {
              key: 'revision',
              label: '版本',
              children: `latest ${revision} / active ${scene.active_revision ?? '-'}`,
            },
            { key: 'source', label: '数据源', children: scene.data_source_name },
            { key: 'view', label: '绑定对象', children: scene.view_name },
          ]}
        />
        <Card
          className='mt-4'
          size='small'
          title='查询限制'
          extra={
            <Button
              size='small'
              disabled={scene.status !== 'inactive'}
              href={
                scene.status === 'inactive'
                  ? `/ask-data/scenes/edit?scene_id=${encodeURIComponent(sceneId)}`
                  : undefined
              }
            >
              编辑限制
            </Button>
          }
        >
          <Descriptions
            size='small'
            column={{ xs: 1, md: 2 }}
            items={queryLimitDefinitions.map(definition => ({
              key: definition.key,
              label: definition.label,
              children: (
                <div>
                  <Typography.Text>{formatLimitValue(definition.key, limits[definition.key])}</Typography.Text>
                  <Typography.Paragraph className='!mb-0 !text-xs' type='secondary'>
                    {definition.description}
                  </Typography.Paragraph>
                </div>
              ),
            }))}
          />
        </Card>
        <Tabs
          className='mt-6'
          items={[
            {
              key: 'documents',
              label: '场景文档',
              children: (
                <div className='grid gap-4 lg:grid-cols-2'>
                  {documentCard('数据字典', documents.data_dictionary_md)}
                  {documentCard('业务语义文档', documents.business_semantics_md)}
                </div>
              ),
            },
            {
              key: 'api',
              label: 'API 调用说明',
              children: apiSpec ? (
                <Card>
                  <Typography.Paragraph>
                    POST <code>{apiSpec.query_api}</code>
                  </Typography.Paragraph>
                  <Typography.Paragraph>
                    当前场景仅允许该稳定查询 API，客户端不传递数据源、视图或 SQL。
                  </Typography.Paragraph>
                  <pre className='overflow-auto'>{JSON.stringify(apiSpec, null, 2)}</pre>
                </Card>
              ) : (
                <Alert type='info' message='API 说明暂不可用' />
              ),
            },
            {
              key: 'test',
              label: '场景测试',
              children: (
                <Card>
                  <Input.TextArea
                    value={question}
                    onChange={event => setQuestion(event.target.value)}
                    autoSize={{ minRows: 3, maxRows: 6 }}
                    placeholder='输入单场景测试问题'
                  />
                  <Button
                    className='my-4'
                    type='primary'
                    disabled={!question.trim()}
                    loading={testing}
                    onClick={() => void runSceneTest()}
                  >
                    执行测试
                  </Button>
                  {result && <ResultDisplay response={result} />}
                </Card>
              ),
            },
          ]}
        />
      </div>
    </main>
  );
}
