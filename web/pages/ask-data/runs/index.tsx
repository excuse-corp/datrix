import AskDataPageShell from '@/components/ask-data/AskDataPageShell';
import { listRuns, type QueryRun } from '@/utils/ask-data';
import { SearchOutlined } from '@ant-design/icons';
import { Input, message, Select, Space, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

export default function AskDataRunsPage() {
  const [items, setItems] = useState<QueryRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<string>();
  const [entryType, setEntryType] = useState<string>();
  const [sceneId, setSceneId] = useState('');
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await listRuns({
        page,
        page_size: 20,
        status,
        entry_type: entryType,
        scene_id: sceneId || undefined,
      });
      setItems(payload.data?.items ?? []);
      setTotal(payload.data?.total ?? 0);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载运行记录失败');
    } finally {
      setLoading(false);
    }
  }, [entryType, page, sceneId, status]);
  useEffect(() => {
    void load();
  }, [load]);
  const columns: ColumnsType<QueryRun> = [
    {
      title: 'Query ID',
      dataIndex: 'query_id',
      render: value => <Link href={`/ask-data/runs/detail?query_id=${encodeURIComponent(value)}`}>{value}</Link>,
    },
    { title: '问题', dataIndex: 'question', ellipsis: true },
    { title: '入口', dataIndex: 'entry_type' },
    {
      title: '状态',
      dataIndex: 'status',
      render: value => (
        <Tag
          color={
            value === 'succeeded'
              ? 'success'
              : value === 'partial_succeeded'
                ? 'warning'
                : value === 'failed'
                  ? 'error'
                  : 'default'
          }
        >
          {value}
        </Tag>
      ),
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      render: value => (value === null || value === undefined ? '-' : `${value} ms`),
    },
    { title: '时间', dataIndex: 'created_at', render: value => new Date(value).toLocaleString() },
  ];
  return (
    <AskDataPageShell title='运行记录' description='追踪每次问数的入口、执行状态、耗时与结果详情。'>
      <section className='rounded-2xl border border-gray-200/80 bg-white p-3 shadow-sm dark:border-white/10 dark:bg-[#1a1b1e]'>
        <Space wrap size={10}>
          <Input.Search
            allowClear
            prefix={<SearchOutlined className='text-gray-400' />}
            placeholder='按场景 ID 筛选'
            className='w-full sm:w-64'
            onSearch={value => {
              setSceneId(value);
              setPage(1);
            }}
          />
          <Select
            allowClear
            placeholder='全部入口'
            className='w-32'
            options={[
              { value: 'main_agent', label: '主问数' },
              { value: 'scene_api', label: '场景 API' },
            ]}
            onChange={value => {
              setEntryType(value);
              setPage(1);
            }}
          />
          <Select
            allowClear
            placeholder='全部状态'
            className='w-40'
            options={['succeeded', 'partial_succeeded', 'clarification_required', 'rejected', 'failed'].map(value => ({
              value,
            }))}
            onChange={value => {
              setStatus(value);
              setPage(1);
            }}
          />
        </Space>
      </section>

      <section className='overflow-hidden rounded-2xl border border-gray-200/80 bg-white shadow-sm dark:border-white/10 dark:bg-[#1a1b1e]'>
        <Table
          className='ask-data-table'
          rowKey='query_id'
          columns={columns}
          dataSource={items}
          loading={loading}
          scroll={{ x: true }}
          pagination={{ current: page, total, pageSize: 20, onChange: value => setPage(value) }}
        />
      </section>
    </AskDataPageShell>
  );
}
