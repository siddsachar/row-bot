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
  body?: Record<string, unknown>,
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
    body: options.method === 'POST' ? JSON.stringify(body ?? {}) : undefined,
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
          : typeof (value as { error?: unknown })?.error === 'string'
            ? (value as { error: string }).error
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

export type AccessRoute = {
  id: string;
  kind: string;
  label: string;
  origin: string;
  available: boolean;
  eligible: boolean;
  detail: string;
  warning: string | null;
};

export type AccessRouteSettings = {
  listen_mode: 'local_only' | 'local_network';
  configured_origins: string[];
  managed_externally: boolean;
  can_manage_routes: boolean;
};

export type AccessInvitation = {
  id: string;
  intended_origin: string;
  session_lifetime: 'trusted' | 'temporary';
  expires_at: string;
  claimed_at: string | null;
  cancelled_at: string | null;
};

export interface AccessInvitationClient {
  routes(signal?: AbortSignal): Promise<AccessRoute[]>;
  routeSettings(signal?: AbortSignal): Promise<AccessRouteSettings>;
  invitations(signal?: AbortSignal): Promise<AccessInvitation[]>;
  create(
    routeId: string,
    lifetime: 'trusted' | 'temporary',
    signal?: AbortSignal,
  ): Promise<{ invitation: AccessInvitation; url: string }>;
  cancel(invitationId: string, signal?: AbortSignal): Promise<void>;
  setListenMode(
    expected: AccessRouteSettings['listen_mode'],
    next: AccessRouteSettings['listen_mode'],
  ): Promise<{ restart_required: boolean }>;
  changeOrigin(
    action: 'add' | 'remove',
    origin: string,
    expected: string[],
  ): Promise<void>;
}

export type TailscaleStatus = {
  state: string;
  installed: boolean;
  signed_in: boolean;
  serve_url: string;
  consent_url: string;
  owned: boolean;
  detail: string;
};
export type TailscaleReceipt = {
  pending?: boolean;
  success?: boolean;
  status?: TailscaleStatus | null;
  error?: string;
  restart_required?: boolean;
};
export interface AccessTailscaleClient {
  status(
    signal?: AbortSignal,
  ): Promise<{ can_manage: boolean; status: TailscaleStatus | null }>;
  check(): Promise<TailscaleStatus>;
  action(
    action: 'enable' | 'disable',
    commandId: string,
  ): Promise<TailscaleReceipt>;
  receipt(commandId: string): Promise<TailscaleReceipt>;
}

function validTailscaleStatus(value: unknown): value is TailscaleStatus {
  if (!value || typeof value !== 'object') return false;
  const row = value as Partial<TailscaleStatus>;
  return (
    typeof row.state === 'string' &&
    row.state.length <= 80 &&
    typeof row.installed === 'boolean' &&
    typeof row.signed_in === 'boolean' &&
    typeof row.serve_url === 'string' &&
    row.serve_url.length <= 2048 &&
    typeof row.consent_url === 'string' &&
    row.consent_url.length <= 2048 &&
    typeof row.owned === 'boolean' &&
    typeof row.detail === 'string' &&
    row.detail.length <= 2000
  );
}

function validTailscaleReceipt(
  value: Record<string, unknown>,
): TailscaleReceipt {
  if (value.pending === true) return { pending: true };
  if (
    typeof value.success !== 'boolean' ||
    (value.status !== null && !validTailscaleStatus(value.status)) ||
    typeof value.error !== 'string' ||
    value.error.length > 2000 ||
    typeof value.restart_required !== 'boolean'
  )
    throw { code: 'dependency_unavailable' };
  return {
    success: value.success,
    status: value.status,
    error: value.error,
    restart_required: value.restart_required,
  };
}

export const accessTailscaleClient: AccessTailscaleClient = {
  async status(signal) {
    const value = await request('/api/access/tailscale', {
      method: 'GET',
      signal,
    });
    if (
      typeof value.can_manage !== 'boolean' ||
      (value.status !== null && !validTailscaleStatus(value.status))
    )
      throw { code: 'dependency_unavailable' };
    return { can_manage: value.can_manage, status: value.status };
  },
  async check() {
    const value = await request('/api/access/tailscale/check', {
      method: 'POST',
    });
    if (!validTailscaleStatus(value.status))
      throw { code: 'dependency_unavailable' };
    return value.status;
  },
  async action(action, commandId) {
    if (!identifier.test(commandId)) throw { code: 'invalid_command' };
    const value = await request(
      '/api/access/tailscale/actions',
      { method: 'POST' },
      { action, command_id: commandId },
    );
    return validTailscaleReceipt(value);
  },
  async receipt(commandId) {
    if (!identifier.test(commandId)) throw { code: 'invalid_command' };
    const value = await request(
      `/api/access/tailscale/actions/${encodeURIComponent(commandId)}`,
      { method: 'GET' },
    );
    return validTailscaleReceipt(value);
  },
};

function validRoute(value: unknown): value is AccessRoute {
  if (!value || typeof value !== 'object') return false;
  const row = value as Partial<AccessRoute>;
  return (
    typeof row.id === 'string' &&
    identifier.test(row.id) &&
    typeof row.kind === 'string' &&
    typeof row.label === 'string' &&
    row.label.length <= 200 &&
    typeof row.origin === 'string' &&
    row.origin.length <= 2048 &&
    typeof row.available === 'boolean' &&
    typeof row.eligible === 'boolean' &&
    typeof row.detail === 'string' &&
    (row.warning === null || typeof row.warning === 'string')
  );
}

function validInvitation(value: unknown): value is AccessInvitation {
  if (!value || typeof value !== 'object') return false;
  const row = value as Partial<AccessInvitation>;
  return (
    typeof row.id === 'string' &&
    identifier.test(row.id) &&
    typeof row.intended_origin === 'string' &&
    (row.session_lifetime === 'trusted' ||
      row.session_lifetime === 'temporary') &&
    typeof row.expires_at === 'string' &&
    (row.claimed_at === null || typeof row.claimed_at === 'string') &&
    (row.cancelled_at === null || typeof row.cancelled_at === 'string')
  );
}

export const accessInvitationClient: AccessInvitationClient = {
  async routes(signal) {
    const value = await request('/api/access/routes', {
      method: 'GET',
      signal,
    });
    if (
      !Array.isArray(value.routes) ||
      value.routes.length > 128 ||
      !value.routes.every(validRoute)
    )
      throw { code: 'dependency_unavailable' };
    return value.routes;
  },
  async routeSettings(signal) {
    const value = await request('/api/access/routes', {
      method: 'GET',
      signal,
    });
    if (
      (value.listen_mode !== 'local_only' &&
        value.listen_mode !== 'local_network') ||
      !Array.isArray(value.configured_origins) ||
      value.configured_origins.length > 128 ||
      !value.configured_origins.every(
        (origin) => typeof origin === 'string' && origin.length <= 2048,
      ) ||
      typeof value.managed_externally !== 'boolean' ||
      typeof value.can_manage_routes !== 'boolean'
    )
      throw { code: 'dependency_unavailable' };
    return {
      listen_mode: value.listen_mode,
      configured_origins: value.configured_origins,
      managed_externally: value.managed_externally,
      can_manage_routes: value.can_manage_routes,
    };
  },
  async invitations(signal) {
    const value = await request('/api/access/invitations', {
      method: 'GET',
      signal,
    });
    if (
      !Array.isArray(value.invitations) ||
      value.invitations.length > 256 ||
      !value.invitations.every(validInvitation)
    )
      throw { code: 'dependency_unavailable' };
    return value.invitations;
  },
  async create(routeId, lifetime, signal) {
    if (
      !identifier.test(routeId) ||
      !['trusted', 'temporary'].includes(lifetime)
    )
      throw { code: 'invalid_command' };
    const value = await request(
      '/api/access/invitations',
      { method: 'POST', signal },
      { route_id: routeId, session_lifetime: lifetime },
    );
    if (
      !validInvitation(value.invitation) ||
      typeof value.invitation_url !== 'string' ||
      value.invitation_url.length > 4096 ||
      !value.invitation_url.startsWith(
        `${value.invitation.intended_origin}/connect?`,
      )
    )
      throw { code: 'dependency_unavailable' };
    return { invitation: value.invitation, url: value.invitation_url };
  },
  async cancel(invitationId, signal) {
    if (!identifier.test(invitationId)) throw { code: 'invalid_command' };
    await request(
      `/api/access/invitations/${encodeURIComponent(invitationId)}/cancel`,
      { method: 'POST', signal },
    );
  },
  async setListenMode(expected, next) {
    const value = await request(
      '/api/access/routes/listen',
      { method: 'POST' },
      { expected_mode: expected, listen_mode: next },
    );
    if (typeof value.restart_required !== 'boolean')
      throw { code: 'dependency_unavailable' };
    return { restart_required: value.restart_required };
  },
  async changeOrigin(action, origin, expected) {
    await request(
      '/api/access/routes/origins',
      { method: 'POST' },
      { action, origin, expected_origins: expected },
    );
  },
};

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
