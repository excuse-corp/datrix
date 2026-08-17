export type AskDataStatus = 'succeeded' | 'partial_succeeded' | 'clarification_required' | 'rejected' | 'failed';
export type SceneStatus = 'draft' | 'active' | 'inactive' | 'invalid';

export type AskDataError = { code?: string; message?: string; path?: string };
export type AskDataEnvelope<T> = {
  request_id?: string;
  status?: AskDataStatus | string;
  data?: T;
  detail?: AskDataError;
  err_msg?: string;
};
export type PagedResponse<T> = { items: T[]; page: number; page_size: number; total: number };

export type MetricOrDimension = { key: string; name?: string; unit?: string; type?: string };
export type SceneSummary = {
  scene_id: string;
  name: string;
  description: string;
  status: SceneStatus;
  data_source_name: string;
  view_name: string;
  latest_revision: number;
  active_revision: number | null;
  current_snapshot_id: string | null;
  query_api: string;
  created_at?: string;
  updated_at?: string;
  capabilities?: { can_do?: string[]; cannot_do?: string[] };
  metrics?: MetricOrDimension[];
  dimensions?: MetricOrDimension[];
  limits?: Record<string, unknown>;
};
export type SceneDraft = Pick<SceneSummary, 'scene_id' | 'name' | 'description' | 'data_source_name' | 'view_name'> & {
  semantic_md: string;
};
export type Capability = Pick<SceneSummary, 'scene_id' | 'name' | 'description' | 'query_api'> & {
  keywords: string[];
};
export type SnapshotSummary = {
  snapshot_id: string;
  scene_id: string;
  scene_revision: number;
  snapshot_status: string;
  source_hashes: Record<string, string>;
  routing_projection?: Record<string, unknown>;
  generated_at?: string;
  activated_at?: string;
};
export type ChartSpec = {
  type: 'metric' | 'line' | 'bar' | 'grouped_bar' | 'donut' | 'table';
  title: string;
  option: { category_key?: string; value_keys?: string[]; unit?: string };
  data: Array<Record<string, unknown>>;
};
export type CombinedResult = {
  mode: string;
  status: string;
  columns: Array<{ key: string; name?: string; type: string; unit?: string }>;
  rows: Array<Record<string, unknown>>;
  warnings?: Array<{ code?: string; message?: string }>;
};
export type SceneResult = {
  task_id: string;
  scene_id: string;
  status: string;
  snapshot_id?: string;
  row_count?: number;
  truncated?: boolean;
  warnings?: string[];
  columns?: Array<{ key: string; name?: string }>;
  rows?: Array<Record<string, unknown>>;
};
export type Clarification = { question?: string; field?: string; options?: string[] };
export type AskDataQueryResponse = {
  request_id?: string;
  status: AskDataStatus;
  query_id?: string;
  conversation_id?: string;
  answer?: string | null;
  clarification?: Clarification | null;
  results?: SceneResult[];
  combined?: CombinedResult | null;
  charts?: ChartSpec[];
  warnings?: Array<string | { code?: string; message?: string }>;
  errors?: AskDataError[];
  plan?: Record<string, unknown>;
  data?: Record<string, unknown>;
};
export type QueryRun = {
  query_id: string;
  entry_type: string;
  question: string;
  status: AskDataStatus;
  snapshot_ids: string[];
  created_at: string;
  duration_ms?: number | null;
};
export type AgentRun = {
  agent_run_id: string;
  scene_id: string;
  revision_id: string;
  snapshot_id: string;
  status: string;
  sql_hash?: string | null;
  knowledge_space_name?: string | null;
  duration_ms?: number | null;
  error_code?: string | null;
  error_message?: string | null;
};
export type AskDataConfig = {
  max_parallel_tasks: number;
  max_parallel_per_datasource: number;
  max_concurrent_queries: number;
  query_queue_timeout_seconds: number;
  task_timeout_seconds: number;
  total_timeout_seconds: number;
  runtime_secrets_exposed: false;
};
export type ApiSpec = {
  scene_id: string;
  query_api: string;
  capabilities: Record<string, unknown>;
  limits: Record<string, unknown>;
};

const createRequestId = () => {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID();

  if (typeof cryptoApi?.getRandomValues === 'function') {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, item => item.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
};

const base = '/api/v1/ask-data';
export class AskDataRequestError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail?: AskDataError,
    public readonly requestId?: string,
  ) {
    super(detail?.message ?? `HTTP ${status}`);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    credentials: 'same-origin',
    headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...(init?.headers ?? {}) },
  });
  const contentType = response.headers.get('content-type') ?? '';
  const payload = contentType.includes('application/json') ? await response.json() : undefined;
  if (!response.ok)
    throw new AskDataRequestError(
      response.status,
      payload?.detail ?? { message: payload?.err_msg ?? `请求失败（HTTP ${response.status}）` },
      payload?.request_id,
    );
  return payload as T;
}
const query = (params: Record<string, string | number | undefined>) => {
  const value = new URLSearchParams();
  Object.entries(params).forEach(([key, item]) => item !== undefined && item !== '' && value.set(key, String(item)));
  return value.toString() ? `?${value}` : '';
};
const mutation = <T>(path: string, method: 'POST' | 'PUT' | 'DELETE', body?: unknown) =>
  request<AskDataEnvelope<T>>(path, {
    method,
    headers: method === 'POST' ? { 'Idempotency-Key': createRequestId() } : undefined,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const listCapabilities = () => request<AskDataEnvelope<{ items: Capability[] }>>('/capabilities');
export const listScenes = (params: Record<string, string | number | undefined> = {}) =>
  request<AskDataEnvelope<PagedResponse<SceneSummary>>>(`/scenes${query({ page: 1, page_size: 20, ...params })}`);
export const getScene = (sceneId: string) =>
  request<AskDataEnvelope<SceneSummary>>(`/scenes/${encodeURIComponent(sceneId)}`);
export const getSemanticMarkdown = (sceneId: string) =>
  request<AskDataEnvelope<{ scene_id: string; revision: number; semantic_md: string }>>(
    `/scenes/${encodeURIComponent(sceneId)}/semantic-md`,
  );
export const getApiSpec = (sceneId: string) =>
  request<AskDataEnvelope<ApiSpec>>(`/scenes/${encodeURIComponent(sceneId)}/api-spec`);
export const createScene = (payload: SceneDraft) => mutation<SceneSummary>('/scenes', 'POST', payload);
export const updateScene = (sceneId: string, payload: Omit<SceneDraft, 'scene_id'>) =>
  mutation<SceneSummary>(`/scenes/${encodeURIComponent(sceneId)}`, 'PUT', payload);
export const saveSemanticMarkdown = (sceneId: string, semantic_md: string) =>
  mutation<SceneSummary>(`/scenes/${encodeURIComponent(sceneId)}/semantic-md`, 'POST', { semantic_md });
export const validateRevision = (sceneId: string, revision: number) =>
  mutation<Record<string, unknown>>(`/scenes/${encodeURIComponent(sceneId)}/revisions/${revision}/validate`, 'POST');
export const changeSceneStatus = (sceneId: string, action: 'disable' | 'enable') =>
  mutation<SceneSummary>(`/scenes/${encodeURIComponent(sceneId)}/${action}`, 'POST');
export const deleteScene = (sceneId: string) =>
  mutation<SceneSummary>(`/scenes/${encodeURIComponent(sceneId)}`, 'DELETE');
export const getActiveSnapshot = (sceneId: string) =>
  request<AskDataEnvelope<SnapshotSummary>>(`/scenes/${encodeURIComponent(sceneId)}/snapshot`);
export const listSnapshots = (sceneId?: string) =>
  request<AskDataEnvelope<PagedResponse<SnapshotSummary>>>(
    `/snapshots${query({ scene_id: sceneId, page: 1, page_size: 100 })}`,
  );
export const submitQuery = (
  payload: {
    question: string;
    conversation_id?: string;
    timezone?: string;
    response_mode?: 'full';
    include_chart?: boolean;
  },
  idempotencyKey: string,
) =>
  request<AskDataQueryResponse>('/query', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey, 'X-Request-Id': createRequestId() },
    body: JSON.stringify({ ...payload, response_mode: 'full', include_chart: true }),
  });
export const submitSceneQuery = (
  sceneId: string,
  payload: { question: string; conversation_id?: string; timezone?: string },
) =>
  request<AskDataQueryResponse>(`/scenes/${encodeURIComponent(sceneId)}/query`, {
    method: 'POST',
    headers: { 'Idempotency-Key': createRequestId(), 'X-Request-Id': createRequestId() },
    body: JSON.stringify({ ...payload, response_mode: 'full', include_chart: true }),
  });
export const replyQuery = (queryId: string, answer: string) =>
  request<AskDataQueryResponse>(`/queries/${encodeURIComponent(queryId)}/reply`, {
    method: 'POST',
    body: JSON.stringify({ answer }),
  });
export const replySceneQuery = (sceneId: string, queryId: string, answer: string) =>
  request<AskDataQueryResponse>(`/scenes/${encodeURIComponent(sceneId)}/queries/${encodeURIComponent(queryId)}/reply`, {
    method: 'POST',
    body: JSON.stringify({ answer }),
  });
export const resetConversation = (conversationId: string) =>
  mutation<Record<string, unknown>>(`/conversations/${encodeURIComponent(conversationId)}/reset`, 'POST');
export const listRuns = (params: Record<string, string | number | undefined> = {}) =>
  request<AskDataEnvelope<PagedResponse<QueryRun>>>(`/runs${query({ page: 1, page_size: 20, ...params })}`);
export const getRun = (queryId: string) =>
  request<AskDataEnvelope<Record<string, unknown>>>(`/runs/${encodeURIComponent(queryId)}`);
export const listAgentRuns = (queryId: string) =>
  request<AskDataEnvelope<{ items: AgentRun[] }>>(`/runs/${encodeURIComponent(queryId)}/agents`);
export const getConfig = () => request<AskDataEnvelope<AskDataConfig>>('/config');
