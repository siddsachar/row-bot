import type { ClientError, ClientStatus } from './types';

const descriptions: Record<string, ClientError> = {
  file_revision_conflict: {
    code: 'file_revision_conflict',
    message:
      'The file changed after review. Its current contents were preserved; reload and review again.',
    recovery: 'review',
  },
  edit_recovery_conflict: {
    code: 'edit_recovery_conflict',
    message:
      'The retained recovery no longer matches these files. Keep the original command and review the workspace.',
    recovery: 'review',
  },
  workspace_undo_not_admitted: {
    code: 'workspace_undo_not_admitted',
    message:
      'No Undo admission was found. Keep the original review while checking its outcome.',
    recovery: 'review',
  },
  workspace_undo_review_changed: {
    code: 'workspace_undo_review_changed',
    message:
      'The files or workspace policy changed. Review the current change set again.',
    recovery: 'review',
  },
  workspace_undo_proof_unavailable: {
    code: 'workspace_undo_proof_unavailable',
    message:
      'The exact retained originals are unavailable. The files were preserved.',
    recovery: 'review',
  },
  workspace_import_not_admitted: {
    code: 'workspace_import_not_admitted',
    message:
      'This import was not admitted. Review the current changes before trying again.',
    recovery: 'review',
  },
  runtime_installation_pending: {
    code: 'runtime_installation_pending',
    message:
      'An installation is still owned. Check its original receipt before starting another.',
    recovery: 'review',
  },
  runtime_plan_changed: {
    code: 'runtime_plan_changed',
    message: 'The runtime changed. Review its current installation again.',
    recovery: 'review',
  },
  process_limit: {
    code: 'process_limit',
    message:
      'The retained process session is full. Finish an owned process before starting another.',
    recovery: 'review',
  },
  process_review_stale: {
    code: 'process_review_stale',
    message:
      'The command or workspace policy changed. Review the current command before starting it.',
    recovery: 'review',
  },
  process_start_unconfirmed: {
    code: 'process_start_unconfirmed',
    message:
      'Start is unconfirmed. Check the original receipt or stop its owned process.',
    recovery: 'review',
  },
  process_cleanup_unconfirmed: {
    code: 'process_cleanup_unconfirmed',
    message:
      'Cleanup is unconfirmed. Recover this exact process before starting another writer.',
    recovery: 'retry',
  },
  share_review_changed: {
    code: 'share_review_changed',
    message:
      'The design or destination changed. Review sharing again before continuing.',
    recovery: 'review',
  },
  invalid_share: {
    code: 'invalid_share',
    message: 'Review the sharing options before continuing.',
    recovery: 'review',
  },
  sharing_busy: {
    code: 'sharing_busy',
    message:
      'Another sharing operation is finishing. Try again after it completes.',
    recovery: 'retry',
  },
  sharing_media_limit: {
    code: 'sharing_media_limit',
    message: 'Choose up to four pages for X.',
    recovery: 'review',
  },
  interactive_publish_requires_all_pages: {
    code: 'interactive_publish_requires_all_pages',
    message: 'Publish all routes of this interactive design together.',
    recovery: 'review',
  },
  sharing_outcome_uncertain: {
    code: 'sharing_outcome_uncertain',
    message:
      'The original sharing outcome is uncertain. Check the destination before another attempt.',
    recovery: 'review',
  },
  channel_unavailable: {
    code: 'channel_unavailable',
    message: 'This channel is unavailable. Review its existing settings.',
    recovery: 'review',
  },
  recipient_unavailable: {
    code: 'recipient_unavailable',
    message: 'Review the destination before sharing.',
    recovery: 'review',
  },
  delivery_unavailable: {
    code: 'delivery_unavailable',
    message: 'This channel does not support the selected delivery format.',
    recovery: 'review',
  },
  task_settings_profile_conflict: {
    code: 'task_settings_profile_conflict',
    message:
      'The workflow profile changed. Review the current profile before saving; your draft is retained.',
    recovery: 'review',
  },
  task_settings_profile_unavailable: {
    code: 'task_settings_profile_unavailable',
    message:
      'The selected workflow profile is unavailable. Choose and review an available profile.',
    recovery: 'review',
  },
  invalid_task_settings: {
    code: 'invalid_task_settings',
    message: 'Review the workflow settings before saving.',
    recovery: 'review',
  },
  task_settings_model_unavailable: {
    code: 'task_settings_model_unavailable',
    message:
      'The selected model is not in the saved catalog. Review model settings.',
    recovery: 'review',
  },
  task_settings_unavailable: {
    code: 'task_settings_unavailable',
    message:
      'These saved workflow settings are unavailable. Existing values are retained.',
    recovery: 'review',
  },
  task_settings_too_large: {
    code: 'task_settings_too_large',
    message:
      'These workflow settings exceed the supported size. Existing values are retained.',
    recovery: 'review',
  },
  task_settings_read_unconfirmed: {
    code: 'task_settings_read_unconfirmed',
    message:
      'The settings were saved, but their current state could not load. Retry the original save to reconcile it.',
    recovery: 'retry',
  },
  invalid_task_graph: {
    code: 'invalid_task_graph',
    message: 'Review the workflow steps and their fields before saving.',
    recovery: 'review',
  },
  task_graph_invalid_reference: {
    code: 'task_graph_invalid_reference',
    message:
      'A workflow branch or output refers to an unavailable step. Update its reference before saving.',
    recovery: 'review',
  },
  task_graph_cycle: {
    code: 'task_graph_cycle',
    message:
      'The selected workflows would recursively run one another. Review the workflow references.',
    recovery: 'review',
  },
  task_graph_missing_subtask: {
    code: 'task_graph_missing_subtask',
    message: 'A referenced workflow is unavailable. Choose a saved workflow.',
    recovery: 'review',
  },
  task_graph_too_large: {
    code: 'task_graph_too_large',
    message:
      'This graph exceeds the supported size. The saved graph is retained.',
    recovery: 'review',
  },
  task_graph_unavailable: {
    code: 'task_graph_unavailable',
    message:
      'This saved graph cannot be edited here. Its existing steps are retained.',
    recovery: 'review',
  },
  task_graph_read_unconfirmed: {
    code: 'task_graph_read_unconfirmed',
    message:
      'The graph was saved but could not be read back. Retry the original save to reconcile it.',
    recovery: 'retry',
  },
  task_policy_revision_conflict: {
    code: 'task_policy_revision_conflict',
    message:
      'The workflow policy changed. Refresh and review it before running.',
    recovery: 'review',
  },
  task_approval_revision_conflict: {
    code: 'task_approval_revision_conflict',
    message: 'This approval changed. Refresh and review the current request.',
    recovery: 'review',
  },
  task_approval_expired: {
    code: 'task_approval_expired',
    message: 'This workflow approval expired. Refresh its saved status.',
    recovery: 'review',
  },
  task_run_unconfirmed: {
    code: 'task_run_unconfirmed',
    message:
      'This run was reserved, but dispatch is unconfirmed. Review its saved history; it will not be started again automatically.',
    recovery: 'review',
  },
  task_approval_unconfirmed: {
    code: 'task_approval_unconfirmed',
    message:
      'The approval was recorded, but continuation is unconfirmed. Review the saved run; the decision will not be repeated.',
    recovery: 'review',
  },
  task_run_not_found: {
    code: 'task_run_not_found',
    message: 'This saved workflow run is unavailable.',
    recovery: 'review',
  },
  task_run_draining: {
    code: 'task_run_draining',
    message:
      'The previous workflow step is still finishing. Refresh before responding.',
    recovery: 'review',
  },
  task_policy_unavailable: {
    code: 'task_policy_unavailable',
    message:
      'The workflow policy is unavailable. Review its saved profile before running.',
    recovery: 'review',
  },
  export_busy: {
    code: 'export_busy',
    message:
      'Another design export is still running. Try again after it finishes.',
    recovery: 'retry',
  },
  invalid_export: {
    code: 'invalid_export',
    message: 'Review the export format and options.',
    recovery: 'review',
  },
  invalid_page_range: {
    code: 'invalid_page_range',
    message: 'Choose page numbers within this design.',
    recovery: 'review',
  },
  export_expired: {
    code: 'export_expired',
    message: 'This download expired. Export the saved design again.',
    recovery: 'review',
  },
  export_capacity_reached: {
    code: 'export_capacity_reached',
    message: 'Export storage is full. Existing copies are preserved.',
    recovery: 'review',
  },
  export_incomplete: {
    code: 'export_incomplete',
    message: 'The export is incomplete. Any partial copy is preserved.',
    recovery: 'review',
  },
  export_unavailable: {
    code: 'export_unavailable',
    message:
      'This export is unavailable. Review the saved design before exporting again.',
    recovery: 'review',
  },
  task_revision_conflict: {
    code: 'task_revision_conflict',
    message:
      'This task changed. Refresh to review it; your draft is preserved.',
    recovery: 'review',
  },
  invalid_task_fields: {
    code: 'invalid_task_fields',
    message: 'Review the task fields before saving.',
    recovery: 'review',
  },
  invalid_task_schedule: {
    code: 'invalid_task_schedule',
    message: 'Review the schedule and timezone before saving.',
    recovery: 'review',
  },
  task_advanced_edit_required: {
    code: 'task_advanced_edit_required',
    message:
      'This task uses advanced workflow steps. Preserve its graph when editing.',
    recovery: 'review',
  },
  task_delivery_review_required: {
    code: 'task_delivery_review_required',
    message:
      'This task has an existing delivery route that needs review before changing.',
    recovery: 'review',
  },
  task_saved_read_unconfirmed: {
    code: 'task_saved_read_unconfirmed',
    message:
      'The task was saved, but its current details could not load. Check the saved task before retrying.',
    recovery: 'review',
  },
  task_not_found: {
    code: 'task_not_found',
    message: 'This task is no longer available.',
    recovery: 'none',
  },
  element_unavailable: {
    code: 'element_unavailable',
    message: 'This design element changed. Refresh before editing it.',
    recovery: 'review',
  },
  invalid_edit: {
    code: 'invalid_edit',
    message: 'Review the design edit before saving.',
    recovery: 'review',
  },
  history_unavailable: {
    code: 'history_unavailable',
    message:
      'This design snapshot is unavailable. The current design is preserved.',
    recovery: 'review',
  },
  editing_record_too_large: {
    code: 'editing_record_too_large',
    message: 'This saved design record exceeds the panel limit.',
    recovery: 'review',
  },
  voice_session_busy: {
    code: 'voice_session_busy',
    message:
      'Voice is active in another window or is still stopping. Finish it there, then try Dictate again.',
    recovery: 'retry',
  },
  voice_session_expired: {
    code: 'voice_session_expired',
    message:
      'This dictation session ended. Start Dictate again when you are ready.',
    recovery: 'retry',
  },
  whisper_model_missing: {
    code: 'whisper_model_missing',
    message:
      'Set up a local speech recognition model in Voice settings before using Dictate.',
    recovery: 'review',
  },
  ffmpeg_unavailable: {
    code: 'ffmpeg_unavailable',
    message:
      'The local audio decoder is unavailable. Review Voice setup before using Dictate.',
    recovery: 'review',
  },
  voice_service_busy: {
    code: 'voice_service_busy',
    message:
      'Speech recognition is busy. Wait for the current operation to finish, then try again.',
    recovery: 'retry',
  },
  audio_receive_timeout: {
    code: 'audio_receive_timeout',
    message:
      'The audio upload timed out. Start a new dictation when the connection is ready.',
    recovery: 'retry',
  },
  audio_decode_timeout: {
    code: 'audio_decode_timeout',
    message: 'Audio decoding timed out. Try a shorter dictation.',
    recovery: 'retry',
  },
  unsupported_audio_type: {
    code: 'unsupported_audio_type',
    message:
      'This audio format is not supported. Try Dictate in another supported browser.',
    recovery: 'review',
  },
  malformed_audio: {
    code: 'malformed_audio',
    message: 'The recording could not be decoded. Start Dictate again.',
    recovery: 'retry',
  },
  empty_audio: {
    code: 'empty_audio',
    message:
      'No audio was recorded. Check microphone permission and try again.',
    recovery: 'review',
  },
  audio_duration_invalid: {
    code: 'audio_duration_invalid',
    message: 'Keep each dictation within 30 seconds.',
    recovery: 'review',
  },
  transcript_too_large: {
    code: 'transcript_too_large',
    message:
      'The transcript exceeded the dictation limit. Try a shorter recording.',
    recovery: 'review',
  },
  voice_operation_failed: {
    code: 'voice_operation_failed',
    message:
      'Speech recognition did not complete. Your current draft is preserved; start Dictate again to retry.',
    recovery: 'retry',
  },
  workspace_name_invalid: {
    code: 'workspace_name_invalid',
    message:
      'Choose a valid new folder name without path separators or reserved names.',
    recovery: 'review',
  },
  workspace_destination_exists: {
    code: 'workspace_destination_exists',
    message:
      'That folder already exists. Register the existing folder, choose another name or location, or cancel.',
    recovery: 'review',
  },
  workspace_destination_not_empty: {
    code: 'workspace_destination_not_empty',
    message:
      'This folder now contains files. Review it before explicitly registering it as an existing folder.',
    recovery: 'review',
  },
  workspace_recovery_conflict: {
    code: 'workspace_recovery_conflict',
    message:
      'The selected parent or created folder changed. Review the original folder before continuing.',
    recovery: 'review',
  },
  workspace_creation_denied: {
    code: 'workspace_creation_denied',
    message:
      'Row-Bot cannot create a folder here. Review folder permissions or choose another parent.',
    recovery: 'review',
  },
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
