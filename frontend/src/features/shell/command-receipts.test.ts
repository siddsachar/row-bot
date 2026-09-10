import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { CommandReceipts, ReceiptStorageError } from './command-receipts';

const receipts = new CommandReceipts(() => sessionStorage);
const claim = () => ({
  commandId: crypto.randomUUID(),
  steeringId: crypto.randomUUID(),
});
beforeEach(() => sessionStorage.clear());
afterEach(() => vi.restoreAllMocks());

it('keeps only identities, scopes host and conversation, and clears only the exact receipt', () => {
  const key = receipts.scope('host-a', 'conversation-a'),
    value = claim();
  receipts.reserve(key, value);
  expect(new CommandReceipts(() => sessionStorage).read(key)).toEqual(value);
  expect(receipts.read(receipts.scope('host-b', 'conversation-a'))).toBeNull();
  expect(receipts.read(receipts.scope('host-a', 'conversation-b'))).toBeNull();
  expect(() => receipts.reserve(key, claim())).toThrow(ReceiptStorageError);
  expect(() => receipts.clear(key, crypto.randomUUID())).toThrow(
    ReceiptStorageError,
  );
  expect(receipts.read(key)).toEqual(value);
  receipts.clear(key, value.commandId);
  expect(receipts.read(key)).toBeNull();
});

it('preserves existing raw New chat IDs and never evicts unresolved receipts at its bound', () => {
  const key = receipts.scope('host'),
    id = crypto.randomUUID();
  sessionStorage.setItem(key, id);
  expect(receipts.read(key)).toEqual({ commandId: id, steeringId: null });
  for (let i = 0; i < 31; i++)
    receipts.reserve(receipts.scope('host', String(i)), claim());
  expect(() =>
    receipts.reserve(receipts.scope('host', 'overflow'), claim()),
  ).toThrow(ReceiptStorageError);
  expect(receipts.read(key)?.commandId).toBe(id);
  expect(sessionStorage.length).toBe(32);
});

it.each(['throw', 'discard'] as const)(
  'fails closed when storage writes %s',
  (mode) => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      if (mode === 'throw') throw new Error('private browser detail');
    });
    expect(() =>
      receipts.reserve(receipts.scope('host', 'a'), claim()),
    ).toThrow(ReceiptStorageError);
  },
);

it('does not mistake inaccessible or malformed storage for an unused identity', () => {
  const key = receipts.scope('host', 'a');
  sessionStorage.setItem(
    key,
    JSON.stringify({ ...claim(), text: 'private content' }),
  );
  expect(() => receipts.read(key)).toThrow(ReceiptStorageError);
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('private');
  });
  expect(() => receipts.read(key)).toThrow(ReceiptStorageError);
});

it('does not report a silently failed removal as cleared', () => {
  const key = receipts.scope('host', 'a'),
    value = claim();
  receipts.reserve(key, value);
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => undefined);
  expect(() => receipts.clear(key, value.commandId)).toThrow(
    ReceiptStorageError,
  );
  expect(receipts.read(key)).toEqual(value);
});

it('isolates ordinary submission identities from steering in the same conversation', () => {
  const submit = receipts.scope('host', 'a', 'submit'),
    steering = receipts.scope('host', 'a');
  const value = claim();
  receipts.reserve(submit, value);
  expect(receipts.read(submit)).toEqual(value);
  expect(receipts.read(steering)).toBeNull();
  expect(sessionStorage.getItem(submit)).not.toContain('text');
});

it('stores only Resume command identity with a strict null submission and shares the unresolved bound', () => {
  const key = receipts.scope('host', 'a', 'resume'),
    value = { commandId: crypto.randomUUID(), steeringId: null };
  expect(() => receipts.reserve(key, claim())).toThrow(ReceiptStorageError);
  receipts.reserve(key, value);
  expect(receipts.read(key)).toEqual(value);
  expect(receipts.read(receipts.scope('host', 'a', 'submit'))).toBeNull();
  expect(receipts.read(receipts.scope('host', 'a'))).toBeNull();
  for (let index = 0; index < 31; index++)
    receipts.reserve(receipts.scope('host', String(index), 'submit'), claim());
  expect(() =>
    receipts.reserve(receipts.scope('host', 'overflow', 'resume'), {
      commandId: crypto.randomUUID(),
      steeringId: null,
    }),
  ).toThrow(ReceiptStorageError);
  receipts.clear(key, value.commandId);
  expect(receipts.read(key)).toBeNull();
});
