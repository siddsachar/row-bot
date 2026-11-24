import { ApiError } from './api';
import type { AuditEvent } from './types';

/** What went wrong, in plain words, for a notice or an error state. */
export function problem(error: unknown): string {
  if (!(error instanceof ApiError))
    return 'Something went wrong. Try again in a moment.';
  const known: Record<string, string> = {
    unreachable:
      "The company server can't be reached. Check your connection and try again.",
    cannot_suspend_yourself: "You can't suspend yourself.",
    cannot_change_your_own_role: "You can't change your own admin role.",
    person_not_found: 'That person is no longer on the server.',
    device_not_found: 'That device is no longer on the server.',
    name_taken: 'That name is already used. Choose another one.',
    endpoint_has_models:
      'This endpoint still has catalog models. Remove them first.',
    anthropic_has_no_embeddings: 'Anthropic endpoints have no embeddings.',
    budget_needs_a_limit: 'Set a warning limit, a stop limit, or both.',
    soft_limit_above_hard_limit:
      'The warning limit must not be above the stop limit.',
    person_budget_needs_a_principal_id: 'Choose a person.',
    end_before_start: 'The end date must be after the start date.',
    version_conflict:
      'Someone else changed this in the meantime. Reload to see their version, then make your change again.',
    policy_too_large: 'The policy is too large to save.',
    invalid_group: 'That group name can’t be used.',
    space_not_found: 'That space is no longer on the server.',
    document_not_found: 'That document is no longer on the server.',
    fact_not_found: 'That fact is no longer on the server.',
    entity_not_found: 'That entry is no longer in the company knowledge.',
    relation_not_found: 'That link is no longer in the company knowledge.',
    space_owner_required: 'Only the space’s owners and admins can change it.',
    unknown_person:
      'One of the owners has never signed in. Choose people who have.',
    company_space_is_read_by_everyone:
      'Everyone signed in reads the company space, so it has no reader groups.',
    company_space_stays: 'The company space can’t be deleted.',
    unknown_source_document:
      'That document isn’t in this space any more. Choose another source.',
    entity_exists:
      'Another entry in this space already has that name and type. Merge the two instead.',
    entity_merged:
      'This entry was merged into another one. Reload to see where it went.',
    unknown_entity:
      'That entry can’t be merged into. Choose another one from this space.',
    too_many_uploads:
      'The server is busy with other uploads. Try again in a moment.',
    unsupported_type: 'Only text, Markdown and Word files can be added.',
    file_too_large: 'The file is larger than the server takes.',
    invalid_file_name:
      'That file name can’t be used. Rename the file and try again.',
    file_required: 'The file didn’t arrive. Try again.',
    invalid_upload: 'The file didn’t arrive. Try again.',
  };
  if (known[error.code]) return known[error.code];
  if (error.code === 'invalid_policy' && typeof error.body.detail === 'string')
    return `The policy can't be saved: ${error.body.detail}.`;
  if (error.status === 422)
    return 'Some of the values aren’t valid. Check them and try again.';
  if (error.status >= 500 || error.status === 0)
    return 'The company server had a problem. Try again in a moment.';
  return 'That didn’t work. Try again in a moment.';
}

export const PLATFORMS: Record<string, string> = {
  windows: 'Windows',
  macos: 'macOS',
  linux: 'Linux',
};

const AMOUNT = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const COUNT = new Intl.NumberFormat();
export const amount = (value: number) => AMOUNT.format(value);
export const count = (value: number) => COUNT.format(value);

/** The audit log's actions as a filter offers them. */
export const ACTION_LABELS: Record<string, string> = {
  'session.signed_in': 'Signed in',
  'session.signed_out': 'Signed out',
  'session.revoked': 'Signed out everywhere',
  'device.joined': 'Device joined',
  'device.removed': 'Device removed or revoked',
  'device.locked_down': 'Device locked down',
  'device.credential_reused': 'Device credential used twice',
  'model.called': 'Model called',
  'tool.called': 'Tool used',
  'policy.changed': 'Policy changed',
  'app.approved': 'App approved',
  'app.pushed': 'App pushed',
  'app.removed': 'App removed',
  'document.uploaded': 'Document uploaded',
  'document.removed': 'Document removed',
  'share.submitted': 'Shared to the company',
  'share.approved': 'Share approved',
  'share.rejected': 'Share rejected',
  'library.published': 'Published to the library',
  'data.exported': 'Data exported',
  'data.deleted': 'Data deleted',
  'role.changed': 'Admin role changed',
  'person.suspended': 'Person suspended',
  'person.resumed': 'Person resumed',
  'org_settings.changed': 'Models or budgets changed',
  'space.changed': 'Knowledge space changed',
  'fact.changed': 'Company fact changed',
  'graph.changed': 'Company graph changed',
};

type Names = (id: string) => string;

function setting(ref: string) {
  if (ref.startsWith('gateway.endpoint.')) return 'a model endpoint';
  if (ref.startsWith('gateway.model.')) return 'a catalog model';
  if (ref.startsWith('gateway.budget.')) return 'a budget';
  return 'a server setting';
}

/** One audit event as a sentence: "Ada signed in", "Sam suspended Ada". */
export function describeEvent(event: AuditEvent, nameOf: Names): string {
  const who =
    event.actor.kind === 'operator'
      ? "The server's operator"
      : event.actor.kind === 'service'
        ? 'The Company Server'
        : nameOf(event.actor.id);
  const target = event.target ? nameOf(event.target.ref) : '';
  const detail = event.details;
  switch (event.action) {
    case 'session.signed_in':
      return event.outcome === 'denied'
        ? `${who} was refused sign-in`
        : `${who} signed in`;
    case 'session.signed_out':
      return detail.reason === 'expired'
        ? `${who}'s session ended`
        : `${who} signed out`;
    case 'session.revoked':
      return `${who} signed ${target} out everywhere`;
    case 'device.joined': {
      const platform = PLATFORMS[String(detail.platform)];
      return `${who} joined a ${platform ? `${platform} ` : ''}device`;
    }
    case 'device.removed':
      return detail.reason === 'revoked'
        ? `${who} revoked a device`
        : `${who} removed a device`;
    case 'device.locked_down':
      return `${who}'s device locked down after being offline too long`;
    case 'device.credential_reused':
      return 'A device was signed out: its sign-in was used twice';
    case 'model.called':
      return `${who} used ${String(detail.model ?? 'a model')} (${count(
        Number(detail.input_tokens ?? 0) + Number(detail.output_tokens ?? 0),
      )} tokens)`;
    case 'tool.called':
      return `${who} used the ${event.target?.ref ?? ''} tool`;
    case 'policy.changed':
      return detail.group
        ? `${who} changed what the ${String(detail.group)} group gets`
        : `${who} changed the company policy`;
    case 'data.exported':
      return detail.scope === 'audit'
        ? `${who} exported the audit log`
        : detail.scope === 'person'
          ? `${who} exported their own data`
          : `${who} exported company data`;
    case 'data.deleted':
      return `${who} deleted ${target}'s data`;
    case 'role.changed':
      return detail.change === 'removed'
        ? `${who} removed ${target}'s admin role`
        : `${who} made ${target} an admin`;
    case 'person.suspended':
      return `${who} suspended ${target}`;
    case 'person.resumed':
      return `${who} resumed ${target}`;
    case 'org_settings.changed': {
      const verb =
        detail.change === 'created'
          ? 'added'
          : detail.change === 'deleted'
            ? 'removed'
            : 'changed';
      return `${who} ${verb} ${setting(event.target?.ref ?? '')}`;
    }
    default:
      return `${who}: ${(ACTION_LABELS[event.action] ?? 'something happened').toLowerCase()}`;
  }
}

export const OUTCOMES: Record<string, string> = {
  failed: 'Failed',
  denied: 'Refused',
  blocked: 'Blocked',
};
