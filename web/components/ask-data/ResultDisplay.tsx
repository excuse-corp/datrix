import type { AskDataQueryResponse, SceneResult } from '@/utils/ask-data';
import { Alert, Card, Collapse, Descriptions, Empty, Table, Tag, Typography } from 'antd';

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
          {item.sql_explanation && (
            <Card className='mt-3' size='small' title='数据口径说明'>
              <div className='flex flex-col gap-3'>
                <Typography.Paragraph className='!mb-0 whitespace-pre-wrap'>
                  {item.sql_explanation.summary}
                </Typography.Paragraph>
                <Descriptions
                  size='small'
                  column={{ xs: 1, md: 2 }}
                  items={[
                    {
                      key: 'source',
                      label: '数据来源',
                      children: item.sql_explanation.source_label ?? item.sql_explanation.source ?? '-',
                    },
                    {
                      key: 'grain',
                      label: '每行代表',
                      children: item.sql_explanation.grain_description ?? item.sql_explanation.grain?.join('、') ?? '-',
                    },
                    {
                      key: 'filters',
                      label: '统计范围',
                      children: item.sql_explanation.filters?.join('；') || '无额外筛选',
                    },
                    {
                      key: 'time_scope',
                      label: '时间范围',
                      children: item.sql_explanation.time_scope?.join('；') || '未识别到时间条件',
                    },
                  ]}
                />
                {!!item.sql_explanation.metrics?.length && (
                  <div>
                    <Typography.Text strong>数字怎么算</Typography.Text>
                    <ul className='mb-0 mt-1 pl-5'>
                      {item.sql_explanation.metrics.map(metric => (
                        <li key={`${metric.key}-${metric.expression ?? ''}`}>
                          {metric.definition ?? metric.label ?? metric.key}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {!!item.sql_explanation.business_rules?.length && (
                  <div>
                    <Typography.Text strong>需要注意</Typography.Text>
                    <ul className='mb-0 mt-1 pl-5'>
                      {item.sql_explanation.business_rules.map(rule => (
                        <li key={rule}>{rule}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {item.sql && (
                  <Collapse
                    items={[
                      {
                        key: 'sql',
                        label: '查看技术细节（SQL）',
                        children: (
                          <Typography.Text code className='block whitespace-pre-wrap'>
                            {item.sql}
                          </Typography.Text>
                        ),
                      },
                    ]}
                  />
                )}
              </div>
            </Card>
          )}
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
