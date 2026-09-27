import { afterEach, expect, it, vi } from 'vitest';
import { createFakePlatform } from './platform/fake';
import {
  NATIVE_RENEW_MS,
  keepNativeLease,
  reloadWhenLeaseLapses,
} from './native-lease';

afterEach(() => vi.useRealTimers());

it('renews a native lease every 20 minutes and when a window comes back', async () => {
  vi.useFakeTimers();
  let clock = 0;
  const controller = {
    nativeAttestation: vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue('fresh-attestation'),
  };
  const renewNative = vi.fn().mockResolvedValue({ status: 'ok', value: {} });
  const release = keepNativeLease(
    controller,
    { renewNative },
    window,
    () => clock,
  );
  clock = NATIVE_RENEW_MS - 1;
  await vi.advanceTimersByTimeAsync(60_000);
  expect(controller.nativeAttestation).not.toHaveBeenCalled();
  clock = NATIVE_RENEW_MS;
  await vi.advanceTimersByTimeAsync(60_000);
  // The first try fails (offline); the next check tries again.
  expect(controller.nativeAttestation).toHaveBeenCalledTimes(1);
  expect(renewNative).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(renewNative).toHaveBeenCalledWith('fresh-attestation');
  // Renewed: nothing more until the next 20 minutes, even on focus.
  window.dispatchEvent(new Event('focus'));
  await vi.advanceTimersByTimeAsync(0);
  expect(renewNative).toHaveBeenCalledTimes(1);
  clock += NATIVE_RENEW_MS;
  window.dispatchEvent(new Event('focus'));
  await vi.advanceTimersByTimeAsync(0);
  expect(renewNative).toHaveBeenCalledTimes(2);
  release();
  clock += NATIVE_RENEW_MS;
  await vi.advanceTimersByTimeAsync(120_000);
  expect(renewNative).toHaveBeenCalledTimes(2);
});

it('does nothing in a browser', () => {
  const controller = { nativeAttestation: vi.fn() };
  const release = keepNativeLease(controller, {});
  window.dispatchEvent(new Event('focus'));
  expect(controller.nativeAttestation).not.toHaveBeenCalled();
  release();
});

it('reloads the desktop Buddy once when its lease has lapsed', async () => {
  let clock = 0;
  const reload = vi.fn();
  const lapsed = {
    status: 'unavailable',
    reason: 'native_proof_required',
  } as const;
  const platform = reloadWhenLeaseLapses(
    createFakePlatform({ buddyPlacement: lapsed, readBuddyTarget: lapsed }),
    reload,
    () => clock,
  );
  await platform.readBuddyTarget();
  await platform.buddyPlacement('dock');
  expect(reload).toHaveBeenCalledTimes(1);
  clock = 61_000;
  await platform.readBuddyTarget();
  expect(reload).toHaveBeenCalledTimes(2);
  const other = reloadWhenLeaseLapses(
    createFakePlatform({
      showMainWindow: {
        status: 'unavailable',
        reason: 'native_operation_unavailable',
      },
    }),
    reload,
  );
  await other.showMainWindow('c1');
  expect(reload).toHaveBeenCalledTimes(2);
});
