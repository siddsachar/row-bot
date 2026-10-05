import { expect, it } from 'vitest';
import contract from '../../../contracts/client-platform/v1/error-codes.json';
import { settingsLeaves } from '../features/settings/model';
import {
  catalogCodes,
  clientError,
  failureStatus,
  rejectedBeforeRunning,
} from './errors';
import { networkFailure } from './network-failure';

const serverCodes = Object.keys(contract.codes);
const JARGON = /\b(receipts?|replay(ed)?|admission|admitted|owned)\b/i;

it('describes every error code the server can send (B131)', () => {
  expect(serverCodes.length).toBeGreaterThan(500);
  const described = new Set(catalogCodes());
  const unmapped = serverCodes.filter((code) => !described.has(code));
  expect(unmapped).toEqual([]);
  for (const code of serverCodes) {
    const error = clientError({
      code,
      status: contract.codes[code as keyof typeof contract.codes],
    });
    // 401 and 403 statuses resolve to their connection sentence on purpose.
    if (
      code !== 'action_denied' &&
      ![401, 403].includes(contract.codes[code as keyof typeof contract.codes])
    )
      expect(error.code).toBe(code);
    expect(error.message).not.toMatch(/could not complete this request/);
  }
});

it('speaks plainly: one sentence per code, no protocol words (U23)', () => {
  for (const code of catalogCodes()) {
    const { message } = clientError({ code });
    expect(message, code).toMatch(/^[A-Z"].*[.!?]$/);
    expect(message, code).not.toMatch(JARGON);
    expect(message, code).not.toContain(code);
  }
});

it('offers fixes that lead somewhere real', () => {
  const leaves = new Set(settingsLeaves.map((leaf) => leaf.id));
  const kinds = new Set<string>();
  for (const code of catalogCodes()) {
    const { action } = clientError({ code });
    if (!action) continue;
    kinds.add(action.kind);
    if (action.kind === 'open_setting') {
      const leaf = action.href.replace(/^\/settings\//, '').split('#')[0];
      expect(leaves.has(leaf as never), `${code} → ${action.href}`).toBe(true);
      expect(action.label, code).toMatch(/^Open [A-Z]/);
    }
  }
  expect([...kinds].sort()).toEqual([
    'choose_model',
    'open_setting',
    'reconnect',
    'retry',
    'send_now',
  ]);
  expect(clientError({ code: 'queue_pending' }).action).toEqual({
    kind: 'send_now',
  });
  expect(clientError({ code: 'model_selection_required' }).action).toEqual({
    kind: 'choose_model',
  });
  expect(clientError({ code: 'session_expired' }).action).toEqual({
    kind: 'reconnect',
  });
});

it('names an unknown code in a Details line instead of hiding it', () => {
  const error = clientError({ code: 'brand_new_server_code', status: 409 });
  expect(error.message).toBe(
    'Something went wrong. Try again. Details: brand_new_server_code',
  );
  expect(error.action).toEqual({ kind: 'retry' });
  expect(clientError(new Error('boom')).message).toBe(
    'Something went wrong. Try again.',
  );
  // Only safe identifiers are echoed.
  expect(clientError({ code: 'Bad <b>code</b>' }).message).toBe(
    'Something went wrong. Try again.',
  );
});

it('drops a request only when the server refused it before anything ran', () => {
  for (const code of [
    'queue_pending',
    'generation_active',
    'model_configuration_required',
    'approval_required',
    'resource_revision_conflict',
  ])
    expect(rejectedBeforeRunning(clientError({ code })), code).toBe(true);
  for (const code of [
    'operation_uncertain',
    'checkpoint_unavailable',
    'resource_setup_partial',
    'task_run_unconfirmed',
    'browser_outcome_uncertain',
    'dependency_unavailable',
    'idempotency_mismatch',
  ])
    expect(rejectedBeforeRunning(clientError({ code })), code).toBe(false);
  expect(rejectedBeforeRunning(clientError(networkFailure('offline')))).toBe(
    false,
  );
  expect(rejectedBeforeRunning(clientError({ code: 'brand_new' }))).toBe(false);
});

it('says Disconnected only for a lost connection, and names a client fault', () => {
  const lost = clientError(networkFailure('Failed to fetch'));
  expect(lost).toMatchObject({
    code: 'network_unavailable',
    message: 'Disconnected. What you last saw is kept.',
  });
  expect(failureStatus(lost)).toBe('disconnected');
  // A TypeError from the client's own code is a bug: reconnecting cannot fix
  // it, so it is reported with a code instead of as a lost connection.
  const fault = clientError(
    new TypeError("Cannot read properties of undefined (reading 'digest')"),
  );
  expect(fault).toMatchObject({
    code: 'request_failed',
    message: 'Something went wrong. Try again. Details: client_error',
  });
  expect(failureStatus(fault)).toBe('fatal');
});
