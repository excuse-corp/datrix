export type OntologyIssue = { code: string; message: string; line?: number; path?: string };

export type OntologyNode = {
  id: string;
  type: 'entity' | 'metric' | 'scene' | 'rule';
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

export type OntologyRevision = {
  ontology_id: string;
  revision: number;
  status: string;
  markdown: string;
  content_hash?: string | null;
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
  source_scene_snapshots: Array<Record<string, string>>;
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
  before?: Record<string, string>;
  after?: Record<string, string>;
};

export type OntologySceneSync = {
  pending_count: number;
  changes: OntologySceneChange[];
  revision: OntologyRevision;
};

type Envelope<T> = { status: string; data: T; detail?: { code?: string; message?: string } };

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
  if (!response.ok)
    throw new OntologyRequestError(response.status, payload?.detail?.message ?? `请求失败（HTTP ${response.status}）`);
  return payload?.data as T;
}

export const getOntology = () =>
  request<{
    draft: OntologyRevision;
    active_snapshot: OntologySnapshot | null;
    source_scenes: Array<Record<string, string>>;
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

export const syncOntologyScenes = (expected_revision: number, apply = false) =>
  request<OntologySceneSync>('/sync-scenes', {
    method: 'POST',
    body: JSON.stringify({ expected_revision, apply }),
  });

export const getOntologyRevisionDiff = (revision: number) => request<OntologySceneSync>(`/revisions/${revision}/diff`);

export const saveOntologyLayout = (revision: number, layout: Record<string, unknown>) =>
  request<{ layout: Record<string, unknown> }>(`/revisions/${revision}/layout`, {
    method: 'PUT',
    body: JSON.stringify({ layout }),
  });
