import { getRun, listAgentRuns, type AgentRun } from '@/utils/ask-data';
import { Alert, Card, Descriptions, Empty, Spin, Table, Tag, Typography, message } from 'antd';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

export default function AskDataRunDetailPage() {
  const router = useRouter();
  const queryId = typeof router.query.query_id === 'string' ? router.query.query_id : '';
  const [run, setRun] = useState<Record<string, unknown> | null>(null);
  const [agents, setAgents] = useState<AgentRun[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if (!queryId) return;
    void Promise.all([getRun(queryId), listAgentRuns(queryId)])
      .then(([runPayload, agentsPayload]) => {
        setRun(runPayload.data ?? null);
        setAgents(agentsPayload.data?.items ?? []);
      })
      .catch(error => message.error(error instanceof Error ? error.message : '加载运行详情失败'))
      .finally(() => setLoading(false));
  }, [queryId]);
  if (!queryId)
    return (
      <main className='p-6'>
        <Empty description='缺少 query_id' />
      </main>
    );
  if (loading)
    return (
      <main className='p-6'>
        <Spin />
      </main>
    );
  if (!run)
    return (
      <main className='p-6'>
        <Alert type='error' message='运行记录不存在或没有权限' />
      </main>
    );
  return (
    <main className='mx-auto max-w-6xl p-4 md:p-6'>
      <Typography.Title level={2}>运行详情</Typography.Title>
      <Descriptions
        bordered
        size='small'
        column={{ xs: 1, md: 2 }}
        items={[
          { key: 'query', label: 'Query ID', children: String(run.query_id ?? queryId) },
          { key: 'status', label: '状态', children: <Tag>{String(run.status ?? '-')}</Tag> },
          { key: 'question', label: '问题', children: String(run.question ?? '-') },
          { key: 'conversation', label: '会话', children: String(run.conversation_id ?? '-') },
          { key: 'combine', label: '组合方式', children: String(run.combine_mode ?? '-') },
          { key: 'duration', label: '总耗时', children: run.duration_ms ? `${run.duration_ms} ms` : '-' },
          {
            key: 'snapshots',
            label: 'Snapshots',
            children: (run.snapshot_ids as string[] | undefined)?.join('、') || '-',
          },
          { key: 'warnings', label: '告警', children: (run.warnings as string[] | undefined)?.join('、') || '-' },
        ]}
      />
      <Card className='mt-5' title='子任务'>
        <Table
          rowKey='agent_run_id'
          size='small'
          scroll={{ x: true }}
          pagination={false}
          dataSource={agents}
          columns={[
            { title: '场景', dataIndex: 'scene_id' },
            { title: 'Revision', dataIndex: 'revision_id' },
            { title: 'Snapshot', dataIndex: 'snapshot_id' },
            { title: '状态', dataIndex: 'status', render: value => <Tag>{value}</Tag> },
            { title: '行数 / SQL Hash', render: (_, item) => item.sql_hash ?? '-' },
            { title: '耗时', dataIndex: 'duration_ms', render: value => (value ? `${value} ms` : '-') },
            {
              title: '错误',
              render: (_, item) => (item.error_code ? `${item.error_code}: ${item.error_message ?? ''}` : '-'),
            },
          ]}
        />
      </Card>
      <Card className='mt-5' title='计划与结果元数据'>
        <pre className='max-h-80 overflow-auto'>
          {JSON.stringify({ plan: run.plan_json, errors: run.errors, clarification: run.clarification }, null, 2)}
        </pre>
      </Card>
    </main>
  );
}
