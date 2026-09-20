export type AccessSession = {
  id: string;
  device_id: string;
  created_at: string;
  last_seen_at: string | null;
  expires_at: string;
  revoked_at: string | null;
  lifetime: 'trusted' | 'temporary' | 'migrated';
};

export type AccessDevice = {
  id: string;
  display_name: string;
  created_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
  user_agent: string | null;
  paired_from: string | null;
  access_route: string | null;
  sessions: AccessSession[];
};

const identifier = /^[A-Za-z0-9_-]{1,128}$/;

async function request(
  path: string,
  options: RequestInit,
): Promise<Record<string, unknown>> {
  const response = await fetch(path, {
    ...options,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...(options.method === 'POST'
        ? { 'Content-Type': 'application/json' }
        : {}),
    },
    body: options.method === 'POST' ? '{}' : undefined,
  });
  const text = await response.text();
  if (text.length > 256 * 1024) throw { code: 'payload_too_large' };
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw { code: 'dependency_unavailable' };
  }
  if (!response.ok || !value || typeof value !== 'object')
    throw {
      code:
        typeof (value as { code?: unknown })?.code === 'string'
          ? (value as { code: string }).code
          : response.status === 401
            ? 'session_expired'
            : 'dependency_unavailable',
      status: response.status,
    };
  return value as Record<string, unknown>;
}

function validSession(value: unknown): value is AccessSession {
  if (!value || typeof value !== 'object') return false;
  const row = value as Partial<AccessSession>;
  return (
    typeof row.id === 'string' &&
    identifier.test(row.id) &&
    typeof row.device_id === 'string' &&
    identifier.test(row.device_id) &&
    typeof row.created_at === 'string' &&
    typeof row.expires_at === 'string' &&
    ['trusted', 'temporary', 'migrated'].includes(String(row.lifetime))
  );
}

function validDevice(value: unknown): value is AccessDevice {
  if (!value || typeof value !== 'object') return false;
  const row = value as Partial<AccessDevice>;
  return (
    typeof row.id === 'string' &&
    identifier.test(row.id) &&
    typeof row.display_name === 'string' &&
    row.display_name.length <= 80 &&
    typeof row.created_at === 'string' &&
    Array.isArray(row.sessions) &&
    row.sessions.length <= 256 &&
    row.sessions.every(validSession)
  );
}

export interface AccessClient {
  devices(signal?: AbortSignal): Promise<AccessDevice[]>;
  refresh(
    signal?: AbortSignal,
  ): Promise<{ renewed: boolean; expires_at: string }>;
  revokeSession(sessionId: string, signal?: AbortSignal): Promise<void>;
  revokeDevice(deviceId: string, signal?: AbortSignal): Promise<void>;
  logout(signal?: AbortSignal): Promise<void>;
}

export const accessClient: AccessClient = {
  async devices(signal) {
    const value = await request('/api/access/devices', {
      method: 'GET',
      signal,
    });
    if (
      !Array.isArray(value.devices) ||
      value.devices.length > 256 ||
      !value.devices.every(validDevice)
    )
      throw { code: 'dependency_unavailable' };
    return value.devices;
  },
  async refresh(signal) {
    const value = await request('/api/access/session/refresh', {
      method: 'POST',
      signal,
    });
    if (
      typeof value.renewed !== 'boolean' ||
      typeof value.expires_at !== 'string'
    )
      throw { code: 'dependency_unavailable' };
    return { renewed: value.renewed, expires_at: value.expires_at };
  },
  async revokeSession(sessionId, signal) {
    if (!identifier.test(sessionId)) throw { code: 'invalid_command' };
    await request(
      `/api/access/sessions/${encodeURIComponent(sessionId)}/revoke`,
      {
        method: 'POST',
        signal,
      },
    );
  },
  async revokeDevice(deviceId, signal) {
    if (!identifier.test(deviceId)) throw { code: 'invalid_command' };
    await request(
      `/api/access/devices/${encodeURIComponent(deviceId)}/revoke`,
      {
        method: 'POST',
        signal,
      },
    );
  },
  async logout(signal) {
    await request('/api/access/logout', { method: 'POST', signal });
  },
};
