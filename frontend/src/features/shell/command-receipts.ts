/** Bounded recovery identities only; never a draft, transcript, or mutation queue. */
/** steeringId holds the exact submission ID for either message command; null for New chat or Resume. */
export type PendingCommand = { commandId: string; steeringId: string | null };
const steeringPrefix = 'row-bot:steering-receipt:v1:';
const submitPrefix = 'row-bot:submit-receipt:v1:';
const resumePrefix = 'row-bot:resume-receipt:v1:';
const newChatPrefix = 'row-bot.new-chat.'; // Preserve already-reserved New chat IDs.
const uuid = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value);

export class ReceiptStorageError extends Error {
  constructor() {
    super(
      'Receipt storage is unavailable. Restore browser storage before sending another action.',
    );
  }
}

export class CommandReceipts {
  constructor(private readonly storage: () => Storage) {}
  scope(
    instance: string,
    conversation: string | null = null,
    kind: 'steering' | 'submit' | 'resume' = 'steering',
  ): string {
    if (
      !instance ||
      instance.length > 128 ||
      (conversation !== null && (!conversation || conversation.length > 128))
    )
      throw new ReceiptStorageError();
    return conversation === null
      ? newChatPrefix + instance
      : (kind === 'submit'
          ? submitPrefix
          : kind === 'resume'
            ? resumePrefix
            : steeringPrefix) +
          encodeURIComponent(instance) +
          ':' +
          encodeURIComponent(conversation);
  }
  read(key: string): PendingCommand | null {
    try {
      if (
        key.length > 1024 ||
        (!key.startsWith(newChatPrefix) &&
          !key.startsWith(steeringPrefix) &&
          !key.startsWith(submitPrefix) &&
          !key.startsWith(resumePrefix))
      )
        throw new ReceiptStorageError();
      const raw = this.storage().getItem(key);
      if (raw === null) return null;
      if (key.startsWith(newChatPrefix)) {
        if (!uuid(raw)) throw new ReceiptStorageError();
        return { commandId: raw, steeringId: null };
      }
      if (raw.length > 512) throw new ReceiptStorageError();
      const value = JSON.parse(raw) as PendingCommand;
      if (
        !value ||
        !uuid(value.commandId) ||
        (key.startsWith(resumePrefix)
          ? value.steeringId !== null
          : !uuid(value.steeringId)) ||
        Object.keys(value).length !== 2
      )
        throw new ReceiptStorageError();
      return { commandId: value.commandId, steeringId: value.steeringId };
    } catch {
      throw new ReceiptStorageError();
    }
  }
  reserve(key: string, value: PendingCommand): void {
    try {
      if (this.read(key)) throw new ReceiptStorageError();
      const isNew = key.startsWith(newChatPrefix);
      if (
        !uuid(value.commandId) ||
        (isNew || key.startsWith(resumePrefix)
          ? value.steeringId !== null
          : !uuid(value.steeringId))
      )
        throw new ReceiptStorageError();
      const storage = this.storage();
      let count = 0;
      for (let index = 0; index < storage.length; index++) {
        const stored = storage.key(index);
        if (
          stored?.startsWith(steeringPrefix) ||
          stored?.startsWith(submitPrefix) ||
          stored?.startsWith(resumePrefix) ||
          stored?.startsWith(newChatPrefix)
        )
          count++;
      }
      if (count >= 32) throw new ReceiptStorageError();
      const raw = isNew ? value.commandId : JSON.stringify(value);
      storage.setItem(key, raw);
      if (storage.getItem(key) !== raw) throw new ReceiptStorageError();
    } catch {
      throw new ReceiptStorageError();
    }
  }
  clear(key: string, commandId: string): void {
    try {
      const current = this.read(key);
      if (!current) return;
      if (current.commandId !== commandId) throw new ReceiptStorageError();
      this.storage().removeItem(key);
      if (this.storage().getItem(key) !== null) throw new ReceiptStorageError();
    } catch {
      throw new ReceiptStorageError();
    }
  }
}

export const commandReceipts = new CommandReceipts(() => window.sessionStorage);
