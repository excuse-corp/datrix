import type { OntologyEdge, OntologyNode } from '@/utils/ontology';
import { Descriptions } from 'antd';

const display = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '未声明';
  if (Array.isArray(value)) return value.length ? value.map(display).join('；') : '未声明';
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
};

export default function OntologyReviewDetails({ object }: { object: OntologyNode | OntologyEdge }) {
  const data = object.data ?? {};
  const metric = object.type === 'metric';
  const aggregationLabels: Record<string, string> = {
    sum: '求和（sum）',
    count: '计数（count）',
    avg: '平均值（avg）',
    min: '最小值（min）',
    max: '最大值（max）',
    count_distinct: '去重计数（count_distinct）',
  };
  const aggregation = aggregationLabels[String(data.aggregation)] ?? data.aggregation;
  const sourceLabels: Record<string, string> = {
    scene_id: '场景',
    snapshot_id: '场景快照',
    section: '文档章节',
    quote: '原文摘录',
  };
  const fields: Array<[string, unknown]> = metric
    ? [
        ['计算公式', data.formula],
        ['聚合方式', aggregation],
        ['来源字段', data.source_field],
        ['单位', data.unit],
        ['统计粒度', data.grain],
        ['时间依据', data.time_basis],
        ['零值处理', data.zero_policy],
        ['分析含义', data.analysis_meaning],
        ['比较条件', data.comparability],
      ]
    : ([
        ['语义标识', data.semantic_key],
        ['来源字段', data.source_field],
        ['聚合方式', aggregation],
        ['涉及场景', data.scenes],
        ['关联依据', data.mapping_source],
        ['关联键', data.join_keys ?? data.business_key],
        ['时间对齐条件', data.time_alignment_conditions],
        ['粒度对齐条件', data.grain_alignment],
        ['允许分析', data.allowed_analyses],
        ['禁止推断', data.forbidden_inferences],
      ].filter(([, value]) => value !== undefined) as Array<[string, unknown]>);
  return (
    <div className='break-words whitespace-pre-wrap'>
      <Descriptions column={1} size='small' bordered>
        <Descriptions.Item label='对象标识'>{object.id}</Descriptions.Item>
        <Descriptions.Item label='定义'>{display(object.description)}</Descriptions.Item>
        {fields.map(([label, value]) => (
          <Descriptions.Item key={label} label={label}>
            {display(value)}
          </Descriptions.Item>
        ))}
        <Descriptions.Item label='来源场景'>
          {display(data.source_scene ?? data.source_scenes ?? data.scenes)}
        </Descriptions.Item>
        <Descriptions.Item label='来源场景快照'>{display(data.scene_snapshot_id)}</Descriptions.Item>
        <Descriptions.Item label='来源证据'>
          {object.provenance?.length
            ? object.provenance.map((source, index) => (
                <div key={index} className='mb-2'>
                  {Object.entries(source).map(([key, value]) => (
                    <div key={key}>
                      {sourceLabels[key] ?? key}：{value}
                    </div>
                  ))}
                </div>
              ))
            : display(data.evidence)}
        </Descriptions.Item>
      </Descriptions>
      {metric && !data.formula && (
        <p className='mt-2 text-xs text-amber-700'>
          当前本体未声明计算公式，请结合已声明的聚合方式和来源材料核对；系统不会补推公式。
        </p>
      )}
    </div>
  );
}
