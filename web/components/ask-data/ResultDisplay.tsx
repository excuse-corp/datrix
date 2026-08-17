import type { AskDataQueryResponse, SceneResult } from '@/utils/ask-data';
import { Alert, Card, Descriptions, Empty, Table, Tag, Typography } from 'antd';

const renderTable = (rows: Array<Record<string, unknown>>, columns?: Array<{ key: string; name?: string }>) => {
  const keys = columns?.length
    ? columns.map(item => item.key)
    : Array.from(new Set(rows.flatMap(row => Object.keys(row))));

  return (
    <Table
      size='small'
      scroll={{ x: true }}
      rowKey={(_, index) => String(index)}
      pagination={false}
      dataSource={rows}
      columns={keys.map(key => ({
        title: columns?.find(item => item.key === key)?.name ?? key,
        dataIndex: key,
        key,
        render: (value: unknown) => formatCellValue(value),
      }))}
    />
  );
};

const formatCellValue = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return (
    <Typography.Text code className='whitespace-pre-wrap'>
      {JSON.stringify(value, null, 2)}
    </Typography.Text>
  );
};

function SceneResultTables({ results }: { results?: SceneResult[] }) {
  const visibleResults = results ?? [];
  if (!visibleResults.length) return <Empty description='没有可展示的查询结果' />;
  return (
    <div className='flex flex-col gap-4'>
      {visibleResults.map((item, index) => (
        <div key={item.task_id} className='rounded border border-gray-100 p-3 dark:border-gray-800'>
          <Typography.Paragraph strong className='!mb-3'>
            查询结果{visibleResults.length > 1 ? ` ${index + 1}` : ''} · {item.status} · {item.row_count ?? 0} 行
          </Typography.Paragraph>
          {item.truncated && <Alert className='mb-3' type='warning' showIcon message='结果已截断' />}
          {item.warnings?.map(warning => (
            <Alert key={warning} className='mb-2' type='warning' showIcon message={warning} />
          ))}
          {item.rows?.length ? renderTable(item.rows, item.columns) : <Empty description='该场景没有可展示的明细行' />}
        </div>
      ))}
    </div>
  );
}

function Conclusion({ response }: { response: AskDataQueryResponse }) {
  const warningMessages = (response.warnings ?? []).map(item =>
    typeof item === 'string' ? item : (item.message ?? item.code ?? '查询告警'),
  );

  return (
    <Card title='结论'>
      <div className='flex flex-col gap-4'>
        {response.answer ? (
          <Typography.Paragraph className='!mb-0 whitespace-pre-wrap'>{response.answer}</Typography.Paragraph>
        ) : (
          <Typography.Paragraph className='!mb-0' type='secondary'>
            暂无结论文本
          </Typography.Paragraph>
        )}
        {warningMessages.map((warning, index) => (
          <Alert key={`${warning}-${index}`} type='warning' showIcon message={warning} />
        ))}
        {response.errors?.map((error, index) => (
          <Alert
            key={`${error.code}-${index}`}
            type='error'
            showIcon
            message={error.code ?? 'QUERY_FAILED'}
            description={error.message}
          />
        ))}
        <SceneResultTables results={response.results} />
      </div>
    </Card>
  );
}

export function ResultDisplay({ response }: { response: AskDataQueryResponse }) {
  return (
    <div className='flex flex-col gap-4'>
      <Card size='small'>
        <Descriptions
          size='small'
          column={{ xs: 1, md: 3 }}
          items={[
            {
              key: 'status',
              label: '状态',
              children: (
                <Tag
                  color={
                    response.status === 'succeeded'
                      ? 'success'
                      : response.status === 'partial_succeeded'
                        ? 'warning'
                        : 'error'
                  }
                >
                  {response.status}
                </Tag>
              ),
            },
            { key: 'query', label: 'Query ID', children: response.query_id ?? '-' },
            { key: 'request', label: 'Request ID', children: response.request_id ?? '-' },
          ]}
        />
      </Card>
      <Conclusion response={response} />
    </div>
  );
}
