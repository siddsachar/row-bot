import { markNetworkFailure } from './network-failure';

/**
 * Editors (row-bot mcp, E4): Claude Code, Cursor, Codex and Zed asking
 * Row-Bot. Each connected editor has its own key in this computer's keychain;
 * nothing here ever carries it.
 */
export type EditorId = 'claude-code' | 'cursor' | 'codex' | 'zed';

export type EditorView = {
  id: EditorId;
  name: string;
  /** Row-Bot may add its entry to this editor's own config file. */
  writes_config: boolean;
  connected: boolean;
  connected_at: string | null;
  last_used_at: string | null;
};

export type EditorSnippet = {
  path: string;
  format: 'json' | 'toml';
  text: string;
};

export type EditorConfigPreview = {
  path: string;
  exists: boolean;
  fingerprint: string;
  format: 'json' | 'toml';
  action: 'add' | 'replace' | 'unchanged' | 'unavailable';
  problem: string | null;
  /** Only Row-Bot's own entry, before and after: never the rest of the file. */
  before: string | null;
  after: string | null;
};

export type EditorConfigWritten = {
  path: string;
  backup_path: string | null;
  changed: boolean;
};

export interface EditorsClient {
  list(signal?: AbortSignal): Promise<EditorView[]>;
  connect(
    id: EditorId,
  ): Promise<{ editor: EditorView; snippet: EditorSnippet }>;
  snippet(id: EditorId, signal?: AbortSignal): Promise<EditorSnippet>;
  preview(id: EditorId, signal?: AbortSignal): Promise<EditorConfigPreview>;
  write(id: EditorId, fingerprint: string): Promise<EditorConfigWritten>;
  revoke(id: EditorId): Promise<void>;
}

const IDS: readonly EditorId[] = ['claude-code', 'cursor', 'codex', 'zed'];

async function request(
  path: string,
  method: 'GET' | 'POST',
  body?: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  let response: Response;
  let text: string;
  try {
    response = await fetch(path, {
      method,
      signal,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(method === 'POST' ? { 'Content-Type': 'application/json' } : {}),
      },
      body: method === 'POST' ? JSON.stringify(body ?? {}) : undefined,
    });
    text = await response.text();
  } catch (error) {
    throw markNetworkFailure(error);
  }
  if (text.length > 512 * 1024) throw { code: 'payload_too_large' };
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw { code: 'dependency_unavailable', status: response.status };
  }
  if (!response.ok || !value || typeof value !== 'object')
    throw {
      code:
        typeof (value as { code?: unknown })?.code === 'string'
          ? (value as { code: string }).code
          : 'dependency_unavailable',
      status: response.status,
    };
  return value as Record<string, unknown>;
}

function editorPath(id: EditorId, rest: string) {
  if (!IDS.includes(id)) throw { code: 'invalid_command' };
  return `/api/editors/${id}/${rest}`;
}

function validEditor(value: unknown): value is EditorView {
  const row = value as Partial<EditorView> | null;
  return (
    !!row &&
    IDS.includes(row.id as EditorId) &&
    typeof row.name === 'string' &&
    typeof row.writes_config === 'boolean' &&
    typeof row.connected === 'boolean'
  );
}

function validSnippet(value: unknown): value is EditorSnippet {
  const row = value as Partial<EditorSnippet> | null;
  return (
    !!row &&
    typeof row.path === 'string' &&
    (row.format === 'json' || row.format === 'toml') &&
    typeof row.text === 'string'
  );
}

export const editorsClient: EditorsClient = {
  async list(signal) {
    const value = await request('/api/editors', 'GET', undefined, signal);
    if (!Array.isArray(value.editors) || !value.editors.every(validEditor))
      throw { code: 'dependency_unavailable' };
    return value.editors;
  },
  async connect(id) {
    const value = await request(editorPath(id, 'connect'), 'POST');
    if (!validEditor(value.editor) || !validSnippet(value.snippet))
      throw { code: 'dependency_unavailable' };
    return { editor: value.editor, snippet: value.snippet };
  },
  async snippet(id, signal) {
    const value = await request(
      editorPath(id, 'snippet'),
      'GET',
      undefined,
      signal,
    );
    if (!validSnippet(value.snippet)) throw { code: 'dependency_unavailable' };
    return value.snippet;
  },
  async preview(id, signal) {
    const value = await request(
      editorPath(id, 'config'),
      'GET',
      undefined,
      signal,
    );
    const preview = value.preview as EditorConfigPreview | undefined;
    if (!preview || typeof preview.fingerprint !== 'string')
      throw { code: 'dependency_unavailable' };
    return preview;
  },
  async write(id, fingerprint) {
    const value = await request(editorPath(id, 'config'), 'POST', {
      fingerprint,
    });
    const written = value.written as EditorConfigWritten | undefined;
    if (!written || typeof written.path !== 'string')
      throw { code: 'dependency_unavailable' };
    return written;
  },
  async revoke(id) {
    await request(editorPath(id, 'revoke'), 'POST');
  },
};
