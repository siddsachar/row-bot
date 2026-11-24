/** The Company Server's connector routes (`/api/admin/connectors`), as the console reads them. */

export type ConnectorKind = 'microsoft' | 'google';
export type ConnectorState = 'active' | 'paused' | 'error';

/** Someone a synced file is shared with whom Row-Bot can't match. */
export type Unmapped = { key: string; name: string; files: number };

/** A connector as admins see it: never its secret, only `has_secret`. */
export type Connector = {
  id: string;
  kind: ConnectorKind;
  space_id: string;
  name: string;
  drive_id: string | null;
  folder_id: string | null;
  tenant_id: string | null;
  /** Microsoft: the app's (client) id. Google: the service account's email. */
  client_id: string;
  subject: string | null;
  has_secret: boolean;
  principal_map: Record<string, string>;
  state: ConnectorState;
  state_reason: string | null;
  last_sync_at: string | null;
  next_sync_at: string | null;
  run_as: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  permissions_checked_at: string | null;
  permissions_note: string | null;
  files: number;
  documents: number;
  skipped: number;
  unmapped_files: number;
  unmapped: Unmapped[];
  failing: number;
  limited: number;
  warning: string | null;
  limit_warning: string | null;
};

/** A knowledge space a connector syncs into (`/api/admin/brain/spaces`). */
export type ConnectorSpace = {
  id: string;
  name: string;
  kind: 'company' | 'group';
};

export type ConnectionTest = { ok: boolean; problem: string | null };
