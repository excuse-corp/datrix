import AskDataPageShell from '@/components/ask-data/AskDataPageShell';
import { changeSceneStatus, deleteScene, listScenes, type SceneSummary } from '@/utils/ask-data';
import { PlusOutlined, SearchOutlined } from '@ant-design/icons';
import { Button, Input, message, Popconfirm, Select, Space, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

export default function SceneListPage() {
  const [items, setItems] = useState<SceneSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState<string>();
  const [dataSourceName, setDataSourceName] = useState('');
  const [page, setPage] = useState(1);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await listScenes({
        keyword,
        status,
        data_source_name: dataSourceName || undefined,
        page,
        page_size: 20,
      });
      setItems(payload.data?.items ?? []);
      setTotal(payload.data?.total ?? 0);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载场景失败');
    } finally {
      setLoading(false);
    }
  }, [dataSourceName, keyword, page, status]);
  useEffect(() => {
    void load();
  }, [load]);
  const columns: ColumnsType<SceneSummary> = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (name, row) => (
        <Link href={`/ask-data/scenes/detail?scene_id=${encodeURIComponent(row.scene_id)}`}>{name}</Link>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      render: value => (
        <Tag color={value === 'active' ? 'success' : value === 'invalid' ? 'error' : 'default'}>{value}</Tag>
      ),
    },
    {
      title: '数据源 / 视图',
      render: (_, row) => (
        <span>
          {row.data_source_name}
          <br />
          {row.view_name}
        </span>
      ),
    },
    { title: 'Revision', render: (_, row) => `${row.latest_revision} / ${row.active_revision ?? '-'}` },
    { title: 'Snapshot', dataIndex: 'current_snapshot_id', render: value => value ?? '-' },
    { title: '更新时间', dataIndex: 'updated_at', render: value => (value ? new Date(value).toLocaleString() : '-') },
    {
      title: '操作',
      render: (_, row) => {
        const hasValidatedLatest = row.active_revision === row.latest_revision && Boolean(row.current_snapshot_id);
        const hasUnvalidatedDraft = row.active_revision !== row.latest_revision;
        const canEdit = row.status === 'inactive';
        const enableDisabled = row.status !== 'active' && !hasValidatedLatest;
        const editTooltip = canEdit ? undefined : '请先停用场景再编辑';
        const enableTooltip =
          row.status === 'active'
            ? undefined
            : hasUnvalidatedDraft
              ? '有未校验草稿，请先校验后启用'
              : !row.current_snapshot_id
                ? '没有可启用的已校验快照'
                : undefined;
        return (
          <Space wrap>
            <Link href={`/ask-data/scenes/detail?scene_id=${encodeURIComponent(row.scene_id)}`}>详情</Link>
            <Tooltip title={editTooltip}>
              <span>
                <Button
                  type='link'
                  disabled={!canEdit}
                  href={canEdit ? `/ask-data/scenes/edit?scene_id=${encodeURIComponent(row.scene_id)}` : undefined}
                >
                  编辑
                </Button>
              </span>
            </Tooltip>
            <Tooltip title={enableTooltip}>
              <span>
                <Button
                  type='link'
                  disabled={enableDisabled}
                  onClick={() =>
                    void changeSceneStatus(row.scene_id, row.status === 'active' ? 'disable' : 'enable')
                      .then(load)
                      .catch(error => message.error(String(error)))
                  }
                >
                  {row.status === 'active' ? '停用' : '启用'}
                </Button>
              </span>
            </Tooltip>
            <Popconfirm
              title='确认软删除该场景？历史记录将保留。'
              onConfirm={() =>
                void deleteScene(row.scene_id)
                  .then(load)
                  .catch(error => message.error(String(error)))
              }
            >
              <Button type='link' danger>
                删除
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];
  return (
    <AskDataPageShell
      title='场景管理'
      description='维护业务场景、语义定义与发布版本；变更会先进入草稿，不影响线上版本。'
      actions={
        <Button type='primary' href='/ask-data/scenes/new' icon={<PlusOutlined />}>
          新建场景
        </Button>
      }
    >
      <section className='rounded-2xl border border-gray-200/80 bg-white p-3 shadow-sm dark:border-white/10 dark:bg-[#1a1b1e]'>
        <Space wrap size={10}>
          <Input.Search
            allowClear
            prefix={<SearchOutlined className='text-gray-400' />}
            placeholder='名称或场景介绍'
            className='w-full sm:w-72'
            onSearch={value => {
              setKeyword(value);
              setPage(1);
            }}
          />
          <Select
            allowClear
            placeholder='全部状态'
            className='w-32'
            options={['draft', 'active', 'inactive', 'invalid'].map(value => ({ value }))}
            onChange={value => {
              setStatus(value);
              setPage(1);
            }}
          />
          <Input.Search
            allowClear
            placeholder='数据源名称'
            className='w-full sm:w-52'
            onSearch={value => {
              setDataSourceName(value);
              setPage(1);
            }}
          />
        </Space>
      </section>

      <section className='overflow-hidden rounded-2xl border border-gray-200/80 bg-white shadow-sm dark:border-white/10 dark:bg-[#1a1b1e]'>
        <Table
          className='ask-data-table'
          rowKey='scene_id'
          loading={loading}
          dataSource={items}
          columns={columns}
          scroll={{ x: true }}
          pagination={{ current: page, total, pageSize: 20, onChange: value => setPage(value) }}
        />
      </section>
    </AskDataPageShell>
  );
}
