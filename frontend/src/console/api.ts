/**
 * The console's one network module: same-origin JSON requests to the Company
 * Server's API (the session cookie goes with them; the browser's Origin
 * header satisfies the server's same-origin rule for changes).
 *
 * A 401 means the session is gone and a 403 `admin_required` that the person
 * is no longer an admin: both are announced once to `onAccessLost`, which the
 * shell turns into its "sign in again" or "You need to be an admin" page.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly body: Record<string, unknown> = {},
  ) {
    super(code);
  }
}

export type AccessLost = 'signed_out' | 'not_admin';
let accessLost: (reason: AccessLost) => void = () => {};

export function onAccessLost(listener: (reason: AccessLost) => void) {
  accessLost = listener;
}

export async function request<T>(
  path: string,
  { method = 'GET', body }: { method?: string; body?: unknown } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'unreachable');
  }
  const text = await response.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // Not JSON (a proxy's error page): the status says what happened.
    data = null;
  }
  if (!response.ok) {
    const fields =
      data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
    const code =
      typeof fields.error === 'string'
        ? fields.error
        : `http_${response.status}`;
    if (response.status === 401) accessLost('signed_out');
    if (response.status === 403 && code === 'admin_required')
      accessLost('not_admin');
    throw new ApiError(response.status, code, fields);
  }
  return data as T;
}

/** A query string from the set values only ("" when none is). */
export function query(values: Record<string, string | number | undefined>) {
  const entries = Object.entries(values).filter(
    (entry): entry is [string, string | number] =>
      entry[1] !== undefined && entry[1] !== '',
  );
  return entries.length
    ? `?${new URLSearchParams(entries.map(([key, value]) => [key, String(value)]))}`
    : '';
}
