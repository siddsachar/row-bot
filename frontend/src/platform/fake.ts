import type { CapabilityResult, ClientPlatform, PlatformInfo } from './types';
import { unavailable } from './types';

type AsyncOperation = Exclude<keyof ClientPlatform, 'moveWindow'>;
export type FakePlatformScript = Partial<{
  [Key in AsyncOperation]: Awaited<ReturnType<ClientPlatform[Key]>>;
}> & { moveWindow?: boolean };

export function createFakePlatform(
  script: FakePlatformScript = {},
): ClientPlatform & { calls: string[] } {
  const calls: string[] = [];
  const result = <T>(
    operation: AsyncOperation,
    fallback: CapabilityResult<T>,
  ): Promise<CapabilityResult<T>> => {
    calls.push(operation);
    return Promise.resolve(
      (script[operation] ?? fallback) as CapabilityResult<T>,
    );
  };
  return {
    calls,
    discover: () =>
      result<PlatformInfo>('discover', {
        status: 'ok',
        value: { kind: 'fake', platform: 'unknown', capabilities: [] },
      }),
    selectFile: () => result('selectFile', unavailable()),
    selectFolder: () => result('selectFolder', unavailable()),
    upload: () => result('upload', unavailable()),
    readClipboard: () => result('readClipboard', unavailable()),
    writeClipboard: () => result('writeClipboard', unavailable()),
    openExternal: () => result('openExternal', unavailable()),
    managedWindow: () => result('managedWindow', unavailable()),
    buddyPlacement: () => result('buddyPlacement', unavailable()),
    publishBuddyTarget: () => result('publishBuddyTarget', unavailable()),
    readBuddyTarget: () => result('readBuddyTarget', unavailable()),
    showMainWindow: () => result('showMainWindow', unavailable()),
    moveWindow: () => {
      calls.push('moveWindow');
      return script.moveWindow ?? false;
    },
    openTerminal: () => result('openTerminal', unavailable()),
    save: () => result('save', unavailable()),
  };
}
