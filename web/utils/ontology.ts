export type OntologyIssue = { code: string; message: string; line?: number; path?: string };

export type OntologyNode = {
  id: string;
  type: 'entity' | 'metric' | 'scene' | 'rule' | 'analysis_dimension' | 'analysis_path' | 'analysis_rule';
  name: string;
  aliases?: string[];
  description?: string;
  data?: Record<string, unknown>;
  provenance?: Array<Record<string, string>>;
};

export type OntologyEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  name: string;
  description?: string;
  data?: Record<string, unknown>;
  provenance?: Array<Record<string, string>>;
};

export type OntologyGraph = { nodes: OntologyNode[]; edges: OntologyEdge[] };

export type OntologySourceRef = {
  scene_id: string;
  scene_name?: string;
  snapshot_id: string;
  revision_id: string;
  semantic_md_hash: string;
};

export type OntologyRevision = {
  ontology_id: string;
  revision: number;
  status: string;
  markdown: string;
  content_hash?: string | null;
  source_scene_snapshots: OntologySourceRef[];
  validation_issues: OntologyIssue[];
  created_by: string;
  created_at: string;
  updated_at: string;
};

export type OntologySnapshot = {
  snapshot_id: string;
  ontology_id: string;
  revision: number;
  status: string;
  content_hash: string;
  source_scene_snapshots: OntologySourceRef[];
  created_at: string;
  activated_at?: string | null;
};

export type OntologyGraphResponse = {
  revision: number;
  valid: boolean;
  graph: OntologyGraph;
  prompt_projection: Record<string, unknown>;
  issues: OntologyIssue[];
  layout: Record<string, unknown>;
};

export type OntologySceneChange = {
  scene_id: string;
  kind: 'added' | 'changed' | 'removed';
  before?: Partial<OntologySourceRef>;
  after?: Partial<OntologySourceRef>;
};

export type OntologySourceRefresh = {
  pending_count: number;
  changes: OntologySceneChange[];
  generation?: {
    mode: 'llm' | 'rules';
    source_count: number;
    entity_count: number;
    metric_count: number;
    relation_count: number;
    analysis_rule_count: number;
    warnings: string[];
  } | null;
  revision: OntologyRevision;
};

type Envelope<T> = {
  status: string;
  data: T;
  detail?: { code?: string; message?: string } | string | Array<Record<string, unknown>>;
  err_msg?: string;
  message?: string;
};

const base = '/api/v1/ask-data/ontology';

export class OntologyRequestError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path = '', init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    credentials: 'same-origin',
    headers: {
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers ?? {}),
    },
  });
  const payload = (await response.json().catch(() => undefined)) as Envelope<T> | undefined;
  if (!response.ok) throw new OntologyRequestError(response.status, errorMessage(payload, response.status));
  return payload?.data as T;
}

function errorMessage(payload: Envelope<unknown> | undefined, status: number) {
  if (!payload) return `请求失败（HTTP ${status}）`;
  if (typeof payload.detail === 'string') return readableMessage(payload.detail);
  if (Array.isArray(payload.detail)) {
    const first = payload.detail[0];
    if (first?.msg) return String(first.msg);
  }
  if (payload.detail && typeof payload.detail === 'object' && 'message' in payload.detail) {
    return String(payload.detail.message);
  }
  if (payload.err_msg) return readableMessage(payload.err_msg);
  if (payload.message) return readableMessage(payload.message);
  return `请求失败（HTTP ${status}）`;
}

function readableMessage(value: string) {
  try {
    const parsed = JSON.parse(value.replace(/'/g, '"')) as { message?: unknown };
    if (parsed && typeof parsed.message === 'string') return parsed.message;
  } catch {
    const match = value.match(/['"]message['"]\s*:\s*['"]([^'"]+)['"]/);
    if (match?.[1]) return match[1];
  }
  return value;
}

export const getOntology = () =>
  request<{
    draft: OntologyRevision;
    active_snapshot: OntologySnapshot | null;
    source_scenes: OntologySourceRef[];
  }>();

export const getOntologyGraph = (revision?: number) =>
  request<OntologyGraphResponse>(`/graph${revision ? `?revision=${revision}` : ''}`);

export const previewOntology = (markdown: string) =>
  request<Pick<OntologyGraphResponse, 'valid' | 'graph' | 'prompt_projection' | 'issues'>>('/compile-preview', {
    method: 'POST',
    body: JSON.stringify({ markdown }),
  });

export const saveOntologyDraft = (markdown: string, expected_revision: number) =>
  request<OntologyRevision>('/draft', {
    method: 'PUT',
    body: JSON.stringify({ markdown, expected_revision }),
  });

export const validateOntologyRevision = (revision: number) =>
  request<{ valid: boolean; issues: OntologyIssue[]; revision: OntologyRevision }>(`/revisions/${revision}/validate`, {
    method: 'POST',
  });

export const buildOntologySnapshot = (revision: number) =>
  request<OntologySnapshot>(`/revisions/${revision}/build-snapshot`, { method: 'POST' });

export const activateOntologySnapshot = (snapshotId: string) =>
  request<OntologySnapshot>(`/snapshots/${encodeURIComponent(snapshotId)}/activate`, { method: 'POST' });

export const listOntologyRevisions = () => request<{ items: OntologyRevision[] }>('/revisions');

export const generateOntologyFromScenes = (expected_revision: number, apply = false, mode?: 'llm' | 'rules') => {
  const payload: { expected_revision: number; apply: boolean; mode?: 'llm' | 'rules' } = { expected_revision, apply };
  if (mode) payload.mode = mode;
  return request<OntologySourceRefresh>('/draft/from-active-scenes', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

export const getOntologyRevisionDiff = (revision: number) =>
  request<OntologySourceRefresh>(`/revisions/${revision}/diff`);

export const saveOntologyLayout = (revision: number, layout: Record<string, unknown>) =>
  request<{ layout: Record<string, unknown> }>(`/revisions/${revision}/layout`, {
    method: 'PUT',
    body: JSON.stringify({ layout }),
  });
