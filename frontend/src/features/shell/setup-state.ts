import type { CommandReceipt, ResourceChoice } from '../../api/types';
import { isCommandReceipt } from '../../api/types';

export type SetupDraft = {
  kind: 'artifact' | 'workspace';
  mode: 'create' | 'existing';
  selected: ResourceChoice | null;
  template: string;
  canvas: string;
  name: string;
  brief: string;
  generate: boolean;
  commandId: string | null;
  receipt: CommandReceipt | null;
  generationId: string | null;
  generationReceipt: CommandReceipt | null;
};
const initial = (): SetupDraft => ({
  kind: 'artifact',
  mode: 'create',
  selected: null,
  template: 'blank_deck',
  canvas: '16:9',
  name: '',
  brief: '',
  generate: false,
  commandId: null,
  receipt: null,
  generationId: null,
  generationReceipt: null,
});
const prefix = 'row-bot:setup:v1:';
const maximum = 120000;
const identifier = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= 128;
function parse(raw: string | null): SetupDraft {
  if (!raw || raw.length > maximum) return initial();
  try {
    const value = JSON.parse(raw) as SetupDraft;
    if (
      !value ||
      !['artifact', 'workspace'].includes(value.kind) ||
      !['create', 'existing'].includes(value.mode) ||
      typeof value.name !== 'string' ||
      value.name.length > 120 ||
      typeof value.brief !== 'string' ||
      value.brief.length > 16000 ||
      typeof value.template !== 'string' ||
      value.template.length > 128 ||
      typeof value.canvas !== 'string' ||
      value.canvas.length > 32 ||
      typeof value.generate !== 'boolean' ||
      (value.commandId !== null && !identifier(value.commandId)) ||
      (value.generationId !== null && !identifier(value.generationId))
    )
      throw new Error();
    if (
      value.selected &&
      (!identifier(value.selected.resource_id) ||
        typeof value.selected.name !== 'string' ||
        value.selected.name.length > 256 ||
        typeof value.selected.revision !== 'string' ||
        value.selected.revision.length > 128 ||
        !['artifact', 'workspace'].includes(value.selected.kind))
    )
      throw new Error();
    // Receipts are display hints until an authenticated receipt read confirms them.
    // Discard corrupt display hints while preserving the reserved operation IDs.
    if (!isCommandReceipt(value.receipt)) value.receipt = null;
    if (!isCommandReceipt(value.generationReceipt))
      value.generationReceipt = null;
    return Object.fromEntries(
      Object.keys(initial()).map((key) => [
        key,
        value[key as keyof SetupDraft],
      ]),
    ) as SetupDraft;
  } catch {
    return initial();
  }
}

/** One bounded presentation owner. No tokens, grants or automatic mutation replay. */
export class SetupSessions {
  private entries = new Map<
    string,
    { value: SetupDraft; listeners: Set<() => void> }
  >();
  constructor(private readonly storage: () => Storage) {}
  scope(instance: string, conversation: string | null): string {
    return (
      prefix +
      encodeURIComponent(instance) +
      ':' +
      encodeURIComponent(conversation ?? 'global')
    );
  }
  private entry(key: string) {
    let entry = this.entries.get(key);
    if (!entry) {
      if (this.entries.size >= 16) {
        const unused = [...this.entries].find(
          ([, item]) => item.listeners.size === 0,
        );
        if (unused) this.entries.delete(unused[0]);
        else throw new Error('setup_capacity');
      }
      let raw: string | null = null;
      try {
        raw = this.storage().getItem(key);
      } catch {
        /* Mutation admission requires a durable write below. */
      }
      entry = { value: parse(raw), listeners: new Set() };
      this.entries.set(key, entry);
    }
    return entry;
  }
  read = (key: string): SetupDraft => this.entry(key).value;
  subscribe(key: string, listener: () => void): () => void {
    const entry = this.entry(key);
    entry.listeners.add(listener);
    return () => entry.listeners.delete(listener);
  }
  update(key: string, patch: Partial<SetupDraft>): SetupDraft {
    const entry = this.entry(key),
      value = { ...entry.value, ...patch };
    const raw = JSON.stringify(value);
    if (raw.length > maximum) throw new Error('setup_capacity');
    const storage = this.storage();
    if (storage.getItem(key) === null) {
      let count = 0;
      for (let index = 0; index < storage.length; index++)
        if (storage.key(index)?.startsWith(prefix)) count++;
      if (count >= 16) throw new Error('setup_capacity');
    }
    storage.setItem(key, raw); // Commit recovery identity before the caller dispatches.
    entry.value = value;
    entry.listeners.forEach((listener) => listener());
    return value;
  }
  reserve(key: string, command: string, generation = false): void {
    const current = this.read(key);
    if (generation ? current.generationId : current.commandId)
      throw new Error('setup_pending');
    this.update(
      key,
      generation ? { generationId: command } : { commandId: command },
    );
  }
  confirm(
    key: string,
    command: string,
    receipt: CommandReceipt,
    generation = false,
  ): void {
    const current = this.read(key);
    if (
      (generation ? current.generationId : current.commandId) !== command ||
      receipt.command_id !== command
    )
      return;
    const previous = generation ? current.generationReceipt : current.receipt;
    if (
      previous?.command_id === command &&
      previous.status !== 'admitting' &&
      receipt.status === 'admitting'
    )
      return;
    if (
      previous?.command_id === command &&
      ['completed', 'accepted', 'rejected'].includes(previous.status) &&
      !['completed', 'accepted', 'rejected'].includes(receipt.status)
    )
      return;
    this.update(key, generation ? { generationReceipt: receipt } : { receipt });
  }
  reset(key: string): void {
    const current = this.read(key);
    if (
      (current.commandId &&
        current.receipt?.status !== 'completed' &&
        current.receipt?.status !== 'rejected') ||
      (current.generationId &&
        (!current.generationReceipt ||
          !['accepted', 'completed', 'rejected'].includes(
            current.generationReceipt.status,
          )))
    )
      throw new Error('setup_pending');
    this.storage().removeItem(key);
    const entry = this.entry(key);
    entry.value = initial();
    entry.listeners.forEach((listener) => listener());
  }
}
export const setupSessions = new SetupSessions(() => window.sessionStorage);
