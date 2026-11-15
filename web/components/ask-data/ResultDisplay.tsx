import type { AskDataQueryResponse, OntologyAnalysisContext, SceneResult } from '@/utils/ask-data';
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
        <OntologyAnalysisBasis context={response.ontology_context ?? response.data?.bundle?.ontology_context} />
        <SceneResultTables results={response.results} />
      </div>
    </Card>
  );
}

function OntologyAnalysisBasis({ context }: { context?: OntologyAnalysisContext | null }) {
  if (!context) return null;
  const bindings = context.matched_bindings ?? [];
  const metrics = context.metrics ?? [];
  const rules = context.result_analysis_rules ?? [];
  const paths = context.cross_scene_analysis_paths ?? [];
  const pathJoinIds = new Set(paths.map(path => `${String(path.id)}_join`));
  const joinEdges = (context.edges ?? []).filter(
    edge => edge.type === 'cross_scene_join' && !pathJoinIds.has(String(edge.id)),
  );
  const bridges = [...paths, ...joinEdges];
  const unmatched = context.unmatched_columns ?? [];
  if (!bindings.length && !metrics.length && !rules.length && !bridges.length && !unmatched.length) return null;

  return (
    <Card
      size='small'
      title='语义分析依据'
      extra={context.revision ? <Typography.Text type='secondary'>Ontology v{context.revision}</Typography.Text> : null}
    >
      <div className='flex flex-col gap-3'>
        {bindings.length > 0 && (
          <div>
            <Typography.Text strong>结果列绑定</Typography.Text>
            <ul className='mb-0 mt-1 pl-5'>
              {bindings.map(binding => (
                <li key={`${binding.scene_id}-${binding.result_key}-${binding.metric_id}`}>
                  {binding.scene_id}.{binding.result_key} → {binding.metric_id}
                  <Typography.Text type='secondary'>（{binding.matched_by}）</Typography.Text>
                </li>
              ))}
            </ul>
          </div>
        )}
        {metrics.length > 0 && (
          <div>
            <Typography.Text strong>指标口径</Typography.Text>
            <ul className='mb-0 mt-1 pl-5'>
              {metrics.map(metric => {
                const details = (metric.data ?? {}) as Record<string, unknown>;
                const meaning = [
                  metric.name,
                  details.analysis_meaning,
                  details.interpretation,
                  details.aggregation ? `聚合：${details.aggregation}` : '',
                  details.unit ? `单位：${details.unit}` : '',
                  details.grain ? `粒度：${details.grain}` : '',
                  details.time_basis ? `时间依据：${details.time_basis}` : '',
                  details.conflict_note ? `口径冲突：${details.conflict_note}` : '',
                ]
                  .filter(Boolean)
                  .join('；');
                return <li key={String(metric.id ?? metric.name)}>{meaning}</li>;
              })}
            </ul>
          </div>
        )}
        {rules.length > 0 && (
          <div>
            <Typography.Text strong>适用的分析规则</Typography.Text>
            <ul className='mb-0 mt-1 pl-5'>
              {rules.map(rule => (
                <li key={String(rule.id)}>
                  {String(
                    (rule.data as Record<string, unknown> | undefined)?.guidance ?? rule.description ?? rule.name ?? '',
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {bridges.length > 0 && (
          <div>
            <Typography.Text strong>跨场景分析桥接</Typography.Text>
            <ul className='mb-0 mt-1 pl-5'>
              {bridges.map(bridge => {
                const data = (bridge.data ?? {}) as Record<string, unknown>;
                const items = (value: unknown) => (Array.isArray(value) ? value.map(String) : []);
                const bridgeScenes = Array.isArray(data.scenes) ? data.scenes.map(String) : [];
                const profiles = (
                  Array.isArray(data.result_profiles)
                    ? data.result_profiles
                    : (context.result_profiles ?? []).filter(profile => bridgeScenes.includes(profile.scene_id))
                ) as Array<Record<string, unknown>>;
                return (
                  <li key={String(bridge.id)}>
                    {String(bridge.name ?? bridge.id)}；映射依据：{String(data.mapping_source ?? '未填写')}； 时间条件：
                    {items(data.time_alignment_conditions).join('、') || '未填写'}； 粒度条件：
                    {items(data.grain_alignment).join('、') || String(data.grain ?? '未填写')}。
                    {items(data.forbidden_inferences).length > 0 && (
                      <Typography.Text type='warning'>
                        {' '}
                        禁止推断：{items(data.forbidden_inferences).join('、')}
                      </Typography.Text>
                    )}
                    {items(data.allowed_analyses).length > 0 && (
                      <div className='text-xs text-gray-500'>允许分析：{items(data.allowed_analyses).join('、')}</div>
                    )}
                    {data.evidence ? <div className='text-xs text-gray-500'>证据：{String(data.evidence)}</div> : null}
                    {profiles.length > 0 && (
                      <div className='text-xs text-gray-500'>
                        本次结果：
                        {profiles
                          .map(profile => {
                            const grain = Array.isArray(profile.grain)
                              ? profile.grain.map(String).join('、')
                              : '未识别';
                            return `${String(profile.scene_id)}（粒度 ${grain}；时间 ${String(profile.time_grain ?? '未识别')}；依据 ${String(profile.time_basis ?? '未识别')}）`;
                          })
                          .join('；')}
                        。需先核对是否满足桥接条件。
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {unmatched.length > 0 && (
          <Alert
            type='warning'
            showIcon
            message='部分结果列没有已确认的本体指标绑定'
            description={`${unmatched.join('、')}；这些列的解释依据仅来自查询结果和数据口径说明。`}
          />
        )}
        {context.ontology_snapshot_id && (
          <Typography.Text className='text-xs' type='secondary'>
            快照：{context.ontology_snapshot_id}
          </Typography.Text>
        )}
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
