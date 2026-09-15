import { parse, stringify } from 'yaml';

export type QueryLimits = {
  max_rows: number;
  max_columns: number;
  max_cell_bytes: number;
  max_result_bytes: number;
  timeout_seconds: number;
  max_spec_attempts: number;
};

export const defaultQueryLimits: QueryLimits = {
  max_rows: 1000,
  max_columns: 100,
  max_cell_bytes: 16_384,
  max_result_bytes: 4 * 1024 * 1024,
  timeout_seconds: 30,
  max_spec_attempts: 2,
};

export const queryLimitDefinitions: Array<{
  key: keyof QueryLimits;
  label: string;
  description: string;
  type: 'number' | 'boolean';
  min?: number;
  max?: number;
  unit?: string;
}> = [
  {
    key: 'max_rows',
    label: '最大返回行数',
    description: '单次场景查询最多返回的数据行数，超过后会截断。',
    type: 'number',
    min: 1,
    max: 100_000,
    unit: '行',
  },
  {
    key: 'max_columns',
    label: '最大返回列数',
    description: '单次结果最多保留的列数，防止宽表结果过大。',
    type: 'number',
    min: 1,
    max: 500,
    unit: '列',
  },
  {
    key: 'max_cell_bytes',
    label: '单元格最大字节数',
    description: '单个单元格文本最大保留字节数，超出会截断。',
    type: 'number',
    min: 256,
    max: 1_048_576,
    unit: 'bytes',
  },
  {
    key: 'max_result_bytes',
    label: '结果最大字节数',
    description: '单次查询结果整体最大字节数，防止返回体过大。',
    type: 'number',
    min: 1024,
    max: 64 * 1024 * 1024,
    unit: 'bytes',
  },
  {
    key: 'timeout_seconds',
    label: '查询超时',
    description: '单场景 SQL 执行超时时间。',
    type: 'number',
    min: 1,
    max: 120,
    unit: '秒',
  },
  {
    key: 'max_spec_attempts',
    label: '查询生成尝试次数',
    description: '查询规格校验失败后允许自动修正并重试的次数上限。',
    type: 'number',
    min: 1,
    max: 5,
    unit: '次',
  },
];

export type SceneEditorValues = {
  name: string;
  description: string;
  data_source_name: string;
  view_name: string;
  data_dictionary_md: string;
  business_semantics_md: string;
  query_limits: QueryLimits;
};

const DATA_DICTIONARY_MARKER = '<!-- dataman:document=data-dictionary -->';
const BUSINESS_SEMANTICS_MARKER = '<!-- dataman:document=business-semantics -->';
const FRONT_MATTER = /^---\s*\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)\s*(?:\r?\n|$)/;

export const defaultAskDataSourceName = 'dataman_data';

export const stripDocumentFrontMatter = (document: string) => document.replace(FRONT_MATTER, '').trim();

export const normalizeQueryLimits = (limits?: Partial<Record<keyof QueryLimits, unknown>>): QueryLimits => {
  const result = { ...defaultQueryLimits };
  queryLimitDefinitions.forEach(definition => {
    const value = limits?.[definition.key];
    const numeric = Number(value);
    result[definition.key] = Number.isFinite(numeric) ? numeric : defaultQueryLimits[definition.key];
  });
  return result;
};

export const buildSemanticMarkdown = (sceneId: string, values: SceneEditorValues) => {
  const config = {
    schema_version: '1',
    scene_id: sceneId,
    name: values.name.trim(),
    description: values.description.trim(),
    keywords: [values.name.trim()],
    data_source: values.data_source_name.trim(),
    view: values.view_name.trim(),
    query: normalizeQueryLimits(values.query_limits),
  };
  const body = [
    DATA_DICTIONARY_MARKER,
    '# 数据字典',
    '',
    values.data_dictionary_md.trim(),
    '',
    BUSINESS_SEMANTICS_MARKER,
    '# 业务语义说明',
    '',
    values.business_semantics_md.trim(),
  ].join('\n');
  return `---\n${stringify(config, { lineWidth: 0 }).trim()}\n---\n\n${body}\n`;
};

const sectionContent = (value: string, heading: string) =>
  value
    .trim()
    .replace(new RegExp(`^#\\s+${heading}\\s*(?:\\r?\\n|$)`), '')
    .trim();

export const parseSemanticMarkdown = (document: string): Partial<SceneEditorValues> => {
  const match = document.match(FRONT_MATTER);
  let config: Record<string, unknown> = {};
  if (match) {
    try {
      config = (parse(match[1]) ?? {}) as Record<string, unknown>;
    } catch {
      config = {};
    }
  }
  const body = match ? document.slice(match[0].length).trim() : document.trim();
  const dictionaryStart = body.indexOf(DATA_DICTIONARY_MARKER);
  const semanticsStart = body.indexOf(BUSINESS_SEMANTICS_MARKER);
  const hasSections = dictionaryStart >= 0 && semanticsStart > dictionaryStart;
  const dictionary = hasSections
    ? sectionContent(
        sectionContent(body.slice(dictionaryStart + DATA_DICTIONARY_MARKER.length, semanticsStart), '视图数据字典'),
        '数据字典',
      )
    : '';
  const semantics = hasSections
    ? sectionContent(body.slice(semanticsStart + BUSINESS_SEMANTICS_MARKER.length), '业务语义说明')
    : body;

  return {
    data_dictionary_md: dictionary,
    business_semantics_md: semantics,
    query_limits: normalizeQueryLimits(
      ((config.query ?? config.query_limits) || {}) as Partial<Record<keyof QueryLimits, unknown>>,
    ),
  };
};

export const defaultSceneEditorValues: Partial<SceneEditorValues> = {
  data_source_name: defaultAskDataSourceName,
  query_limits: defaultQueryLimits,
};
