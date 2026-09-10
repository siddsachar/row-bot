import type { ClientError, ClientStatus } from './types';

const descriptions: Record<string, ClientError> = {
  reasoning_model_mismatch: {
    code: 'reasoning_model_mismatch',
    message: 'The selected model changed. Review its current Thinking choices.',
    recovery: 'review',
  },
  invalid_reasoning_selection: {
    code: 'invalid_reasoning_selection',
    message:
      'This model does not support that Thinking choice. Choose a current option.',
    recovery: 'review',
  },
  reasoning_capabilities_changed: {
    code: 'reasoning_capabilities_changed',
    message:
      'Thinking choices changed. Reopen the conversation and review its current choices.',
    recovery: 'review',
  },
  reasoning_snapshot_unavailable: {
    code: 'reasoning_snapshot_unavailable',
    message:
      'The accepted Thinking choice could not be recovered. Review the paused or queued request before continuing.',
    recovery: 'review',
  },
  approval_expired: {
    code: 'approval_expired',
    message:
      'This approval expired. Close this review and open the current approval before deciding.',
    recovery: 'review',
  },
  approval_already_resolved: {
    code: 'approval_already_resolved',
    message:
      'This approval was already resolved. Close this review and check the current activity.',
    recovery: 'review',
  },
  model_configuration_required: {
    code: 'model_configuration_required',
    message:
      'Choose a configured model in the conversation controls before sending. Your draft is preserved.',
    recovery: 'review',
  },
  workspace_read_hooks_unavailable: {
    code: 'workspace_read_hooks_unavailable',
    message:
      'This folder uses Git read hooks that the read-only inspector cannot run. Review its Git configuration.',
    recovery: 'review',
  },
  profile_unavailable: {
    code: 'profile_unavailable',
    message:
      'The selected profile is no longer available. Choose an available profile and review its controls.',
    recovery: 'review',
  },
  profile_snapshot_unavailable: {
    code: 'profile_snapshot_unavailable',
    message:
      'The accepted profile snapshot could not be recovered. Review this queued message before continuing.',
    recovery: 'review',
  },
  invalid_command: {
    code: 'invalid_command',
    message: 'This request could not be validated. Review its inputs.',
    recovery: 'review',
  },
  draft_revision_conflict: {
    code: 'draft_revision_conflict',
    message:
      'This draft changed in another client. Review the saved draft before replacing it.',
    recovery: 'review',
  },
  resource_revision_conflict: {
    code: 'resource_revision_conflict',
    message: 'This resource changed. Reload and review your action.',
    recovery: 'review',
  },
  origin_repair_required: {
    code: 'origin_repair_required',
    message:
      'The original conversation is unavailable. Review the repair action.',
    recovery: 'review',
  },
  capability_unavailable: {
    code: 'capability_unavailable',
    message: 'This action is unavailable on this host.',
    recovery: 'none',
  },
  authentication_required: {
    code: 'authentication_required',
    message: 'Connect to Row-Bot to continue.',
    recovery: 'authenticate',
  },
  session_expired: {
    code: 'session_expired',
    message: 'Your connection has expired. Connect again.',
    recovery: 'authenticate',
  },
  action_denied: {
    code: 'action_denied',
    message: 'This action is unavailable for this connection.',
    recovery: 'authenticate',
  },
  protocol_incompatible: {
    code: 'protocol_incompatible',
    message: 'This client needs an update to connect.',
    recovery: 'update',
  },
  revision_conflict: {
    code: 'revision_conflict',
    message: 'This resource changed. Reload and review your action.',
    recovery: 'review',
  },
  idempotency_mismatch: {
    code: 'idempotency_mismatch',
    message:
      'This action identity was already used. Review the original action.',
    recovery: 'review',
  },
  operation_uncertain: {
    code: 'operation_uncertain',
    message:
      'The action outcome is unknown. Check its receipt before trying again.',
    recovery: 'review',
  },
  not_found: {
    code: 'not_found',
    message: 'This resource is no longer available.',
    recovery: 'none',
  },
  cursor_expired: {
    code: 'cursor_expired',
    message: 'This view expired. Reload the current view.',
    recovery: 'retry',
  },
  payload_too_large: {
    code: 'payload_too_large',
    message: 'This item exceeds the supported size.',
    recovery: 'none',
  },
  network_unavailable: {
    code: 'network_unavailable',
    message: 'Disconnected. Your last confirmed view is preserved.',
    recovery: 'retry',
  },
};

const resourceDenials = new Set([
  'workspace_read_hooks_unavailable',
  'resource_binding_revoked',
  'workspace_path_denied',
  'folder_selection_denied',
  'path_denied',
  'capability_unavailable',
]);

/** Never display a server title, arbitrary exception message, path or response body. */
export function clientError(value: unknown): ClientError {
  const candidate =
    value && typeof value === 'object'
      ? (value as { code?: unknown; status?: unknown })
      : {};
  if (candidate.status === 401) return descriptions.session_expired;
  if (typeof candidate.code === 'string' && resourceDenials.has(candidate.code))
    return (
      descriptions[candidate.code] ?? {
        code: candidate.code,
        message:
          'This resource action is unavailable. Refresh the resource and review its permissions.',
        recovery: 'review',
      }
    );
  if (candidate.status === 403) return descriptions.action_denied;
  if (candidate.status === 426) return descriptions.protocol_incompatible;
  if (typeof candidate.code === 'string' && descriptions[candidate.code])
    return descriptions[candidate.code];
  if (value instanceof Error && value.message === 'protocol_incompatible')
    return descriptions.protocol_incompatible;
  if (value instanceof TypeError) return descriptions.network_unavailable;
  return {
    code: 'request_failed',
    message: 'Row-Bot could not complete this request.',
    recovery: 'retry',
  };
}

export function failureStatus(error: ClientError): ClientStatus {
  if (error.recovery === 'authenticate') return 'unauthorized';
  if (error.recovery === 'update') return 'incompatible';
  return error.code === 'network_unavailable' ? 'disconnected' : 'fatal';
}

export function aborted(value: unknown): boolean {
  return value instanceof DOMException && value.name === 'AbortError';
}
