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

export type OntologyNeo4jHealth = {
  enabled: boolean;
  status: 'ready' | 'disabled' | 'unavailable' | string;
  uri?: string;
  database?: string;
  ready?: boolean;
};

export type OntologySnapshotSyncStatus = {
  enabled: boolean;
  status: 'ready' | 'active' | 'superseded' | 'missing' | 'disabled' | string;
  snapshot_id: string;
  content_hash?: string;
  revision?: number;
  ontology_id?: string;
  node_count?: number;
  relationship_count?: number;
  layer_counts?: Record<
    'business_semantics' | 'analysis_explanations' | 'mapping_governance',
    {
      nodes: number;
      relationships: number;
    }
  >;
  sync_error?: string | null;
};

export type OntologyGraphResponse = {
  revision: number;
  valid: boolean;
  graph: OntologyGraph;
  prompt_projection: Record<string, unknown>;
  issues: OntologyIssue[];
  layout: Record<string, unknown>;
};

export type OntologyNeighborhoodResponse = {
  revision: number;
  center_node_id: string;
  total_node_count: number;
  total_edge_count: number;
  graph: OntologyGraph;
};

export type OntologyReviewItem = {
  target_type: 'node' | 'edge';
  target_id: string;
  object_type: string;
  name: string;
  fingerprint: string;
  object?: OntologyNode | OntologyEdge;
  status: 'candidate' | 'confirmed' | 'rejected' | string;
  required: boolean;
  comment: string;
  reviewer_id?: string | null;
  reviewed_at?: string | null;
};

export type OntologyReviewEvent = {
  event_id: string;
  revision: number;
  event_type: string;
  target_type?: string | null;
  target_id?: string | null;
  decision?: string | null;
  comment?: string;
  actor_id: string;
  content_hash?: string | null;
  created_at: string;
};

export type OntologyReviewState = {
  revision: number;
  items: OntologyReviewItem[];
  required_count: number;
  confirmed_required_count: number;
  pending_count: number;
  current_content_hash?: string | null;
  approved: boolean;
  approved_by?: string | null;
  approved_at?: string | null;
  blocking_issue?: { code: string; message: string };
  events: OntologyReviewEvent[];
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

export const getOntologyGraphNeighborhood = (nodeId: string, revision?: number, nodeLimit = 120, edgeLimit = 300) => {
  const params = new URLSearchParams({ node_id: nodeId, node_limit: String(nodeLimit), edge_limit: String(edgeLimit) });
  if (revision) params.set('revision', String(revision));
  return request<OntologyNeighborhoodResponse>(`/graph/neighborhood?${params.toString()}`);
};

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

export const getOntologyNeo4jHealth = () => request<OntologyNeo4jHealth>('/neo4j/health');

export const getOntologySnapshotSyncStatus = (snapshotId: string) =>
  request<OntologySnapshotSyncStatus>(`/snapshots/${encodeURIComponent(snapshotId)}/sync-status`);

export const syncOntologySnapshot = (snapshotId: string) =>
  request<OntologySnapshotSyncStatus>(`/snapshots/${encodeURIComponent(snapshotId)}/sync`, { method: 'POST' });

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

export const getOntologyReviewState = (revision: number) =>
  request<OntologyReviewState>(`/revisions/${revision}/reviews`);

export const reviewOntologyObject = (
  revision: number,
  payload: Pick<OntologyReviewItem, 'target_type' | 'target_id' | 'fingerprint'> & {
    decision: 'confirmed' | 'rejected' | 'candidate';
    comment?: string;
  },
) =>
  request<{ event: OntologyReviewEvent; review_state: OntologyReviewState }>(`/revisions/${revision}/reviews`, {
    method: 'POST',
    body: JSON.stringify({
      target_type: payload.target_type,
      target_id: payload.target_id,
      decision: payload.decision,
      expected_fingerprint: payload.fingerprint,
      comment: payload.comment ?? '',
    }),
  });

export const excludeOntologyObject = (
  revision: number,
  payload: Pick<OntologyReviewItem, 'target_type' | 'target_id' | 'fingerprint'> & {
    comment: string;
  },
) =>
  request<{
    event: OntologyReviewEvent;
    revision: OntologyRevision;
    removed_target_type: OntologyReviewItem['target_type'];
    removed_target_id: string;
    review_state: OntologyReviewState;
  }>(`/revisions/${revision}/reviews/exclude`, {
    method: 'POST',
    body: JSON.stringify({
      target_type: payload.target_type,
      target_id: payload.target_id,
      expected_fingerprint: payload.fingerprint,
      comment: payload.comment,
    }),
  });

export const approveOntologyRevision = (revision: number, expected_content_hash: string) =>
  request<{ event: OntologyReviewEvent; review_state: OntologyReviewState }>(`/revisions/${revision}/approve`, {
    method: 'POST',
    body: JSON.stringify({ expected_content_hash }),
  });

export const saveOntologyLayout = (revision: number, layout: Record<string, unknown>) =>
  request<{ layout: Record<string, unknown> }>(`/revisions/${revision}/layout`, {
    method: 'PUT',
    body: JSON.stringify({ layout }),
  });
