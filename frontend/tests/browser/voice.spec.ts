import { accessibility, expect, screenshot, test } from './evidence';
import {
  blockFixtureServiceWorkers,
  composer,
  fixtureState,
  newConversation,
  releaseProducer,
} from './unified-helpers';

test.beforeEach(async ({ context, page }) => {
  await blockFixtureServiceWorkers(context);
  await page.addInitScript(() => {
    const audit = {
      acquired: 0,
      stopped: 0,
      denied: false,
      played: 0,
      sent: [] as string[],
      peerClosed: 0,
    };
    Object.assign(window, { __voiceFixture: audit });
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => {
          if (audit.denied)
            throw new DOMException('Synthetic denial', 'NotAllowedError');
          audit.acquired++;
          let stopped = false;
          return {
            getTracks: () => [
              {
                stop: () => {
                  if (!stopped) audit.stopped++;
                  stopped = true;
                },
              },
            ],
          };
        },
      },
    });
    class SyntheticRecorder {
      static isTypeSupported() {
        return true;
      }
      state = 'inactive';
      ondataavailable?: (event: { data: Blob }) => void;
      onstop?: () => void;
      start() {
        this.state = 'recording';
      }
      stop() {
        if (this.state !== 'recording') return;
        this.state = 'inactive';
        this.ondataavailable?.({
          data: new Blob(['synthetic browser audio'], { type: 'audio/webm' }),
        });
        this.onstop?.();
      }
    }
    Object.defineProperty(window, 'MediaRecorder', {
      configurable: true,
      value: SyntheticRecorder,
    });
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      value: undefined,
    });
    class SyntheticAudio {
      onended?: () => void;
      onerror?: () => void;
      pause() {}
      async play() {
        audit.played++;
        queueMicrotask(() => this.onended?.());
      }
    }
    Object.defineProperty(window, 'Audio', {
      configurable: true,
      value: SyntheticAudio,
    });
    class SyntheticPeer {
      listeners = new Map<string, (event?: unknown) => void>();
      constructor() {
        Object.assign(window, { __voicePeer: this });
      }
      addTrack() {}
      close() {
        audit.peerClosed++;
      }
      createDataChannel() {
        return {
          readyState: 'open',
          addEventListener: (name: string, fn: (event?: unknown) => void) =>
            this.listeners.set(name, fn),
          send: (value: string) => audit.sent.push(value),
          close() {},
        };
      }
      async createOffer() {
        return { sdp: 'v=0\r\nsynthetic-browser-offer' };
      }
      async setLocalDescription() {}
      async setRemoteDescription() {
        this.listeners.get('open')?.();
      }
      deliver(payload: unknown) {
        this.listeners.get('message')?.({ data: JSON.stringify(payload) });
      }
    }
    Object.defineProperty(window, 'RTCPeerConnection', {
      configurable: true,
      value: SyntheticPeer,
    });
  });
});

test('Talk submits to the unified conversation and speaks only its saved final response', async ({
  page,
}, info) => {
  const id = await newConversation(page);
  await composer(page).fill('Retain my typed draft.');
  await page.getByRole('button', { name: 'Talk', exact: true }).click();
  const panel = page.getByRole('region', { name: 'Conversation voice' });
  await expect(
    panel.getByText('No resource write targets are selected.'),
  ).toBeVisible();
  await panel.getByRole('button', { name: 'Talk', exact: true }).click();
  await expect(
    panel.getByRole('button', { name: 'Send speech', exact: true }),
  ).toBeVisible();
  await screenshot(page, info, 'talk-recording-unified-conversation');
  await accessibility(page, info, 'talk-recording-unified-conversation');
  await panel.getByRole('button', { name: 'Send speech', exact: true }).click();
  await expect
    .poll(
      async () =>
        (await fixtureState(page)).calls.filter(
          (call) => call.conversation_id === id,
        ).length,
    )
    .toBe(1);
  const call = (await fixtureState(page)).calls.find(
    (call) => call.conversation_id === id,
  )!;
  await releaseProducer(page, call);
  await expect(page.getByRole('log', { name: 'Conversation' })).toContainText(
    'Synthetic stream settled.',
  );
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { __voiceFixture: { played: number } })
            .__voiceFixture.played,
      ),
    )
    .toBe(1);
  await expect(
    panel.getByRole('button', { name: 'Send speech', exact: true }),
  ).toBeVisible();
  await panel.getByRole('button', { name: 'Stop Talk', exact: true }).click();
  await expect(
    panel.getByRole('button', { name: 'Talk', exact: true }),
  ).toBeEnabled();
  await expect(composer(page)).toHaveValue('Retain my typed draft.');
  await expect(composer(page)).toHaveCount(1);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const value = (
          window as unknown as {
            __voiceFixture: { acquired: number; stopped: number };
          }
        ).__voiceFixture;
        return value.acquired - value.stopped;
      }),
    )
    .toBe(0);
  await screenshot(page, info, 'talk-final-response-in-chat');
});

test('Realtime Talk exchanges through the host and consults the same saved conversation', async ({
  page,
}, info) => {
  const remote: string[] = [];
  page.on('request', (request) => {
    if (request.url().startsWith('https://api.openai.com/'))
      remote.push(request.url());
  });
  const id = await newConversation(page);
  await composer(page).fill('Retain the realtime draft.');
  await page.getByRole('button', { name: 'Talk', exact: true }).click();
  const panel = page.getByRole('region', { name: 'Conversation voice' });
  await panel
    .getByRole('combobox', { name: 'Talk mode' })
    .selectOption('realtime');
  await panel.getByRole('button', { name: 'Talk', exact: true }).click();
  await expect(panel.getByRole('status')).toHaveText('Listening…');
  await screenshot(page, info, 'realtime-listening-unified-conversation');
  await accessibility(page, info, 'realtime-listening-unified-conversation');
  await page.evaluate(() => {
    const peer = (
      window as unknown as { __voicePeer: { deliver(payload: unknown): void } }
    ).__voicePeer;
    peer.deliver({
      type: 'response.output_item.done',
      response_id: 'response-one',
      item: {
        type: 'function_call',
        call_id: 'call-one',
        name: 'row_bot_agent_consult',
        arguments: '{"request":"Explain this synthetic fixture"}',
      },
    });
  });
  await expect
    .poll(
      async () =>
        (await fixtureState(page)).calls.filter(
          (call) => call.conversation_id === id,
        ).length,
    )
    .toBe(1);
  await releaseProducer(
    page,
    (await fixtureState(page)).calls.find(
      (call) => call.conversation_id === id,
    )!,
  );
  await expect(page.getByRole('log', { name: 'Conversation' })).toContainText(
    'Synthetic stream settled.',
  );
  await expect
    .poll(() =>
      page.evaluate(() =>
        (
          window as unknown as { __voiceFixture: { sent: string[] } }
        ).__voiceFixture.sent.join('\n'),
      ),
    )
    .toContain('Synthetic stream settled.');
  await panel.getByRole('button', { name: 'Stop Talk', exact: true }).click();
  await expect(
    panel.getByRole('button', { name: 'Talk', exact: true }),
  ).toBeEnabled();
  await expect(composer(page)).toHaveValue('Retain the realtime draft.');
  await expect(composer(page)).toHaveCount(1);
  expect(remote).toEqual([]);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const value = (
          window as unknown as {
            __voiceFixture: { acquired: number; stopped: number };
          }
        ).__voiceFixture;
        return value.acquired - value.stopped;
      }),
    )
    .toBe(0);
  await screenshot(page, info, 'realtime-final-response-in-chat');
});

test('Dictation appends to the current draft once and cancellation releases browser capture', async ({
  page,
}, info) => {
  await newConversation(page);
  const before = await fixtureState(page);
  await composer(page).fill('Typed before recording.');
  await page.getByRole('button', { name: 'Dictate', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Finish dictation', exact: true }),
  ).toBeVisible();
  await composer(page).fill('Edited while recording.');
  await screenshot(page, info, 'dictation-recording');
  await accessibility(page, info, 'dictation-recording');
  await page
    .getByRole('button', { name: 'Finish dictation', exact: true })
    .click();
  await expect(composer(page)).toHaveValue(
    'Edited while recording. Browser dictated fixture text.',
  );
  await expect(
    page.getByRole('button', { name: 'Dictate', exact: true }),
  ).toBeEnabled();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { __voiceFixture: { stopped: number } })
            .__voiceFixture.stopped,
      ),
    )
    .toBe(1);
  await page.getByRole('button', { name: 'Dictate', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Finish dictation', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Cancel dictation', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Dictate', exact: true }),
  ).toBeEnabled();
  await expect(composer(page)).toHaveValue(
    'Edited while recording. Browser dictated fixture text.',
  );
  await expect(composer(page)).toHaveCount(1);
  expect(
    await page.evaluate(
      () =>
        (
          window as unknown as {
            __voiceFixture: { acquired: number; stopped: number };
          }
        ).__voiceFixture,
    ),
  ).toMatchObject({ acquired: 2, stopped: 2 });
  expect((await fixtureState(page)).calls).toHaveLength(before.calls.length);
  await screenshot(page, info, 'dictation-draft-retained');
});

test('Denied browser microphone permission keeps the draft and permits recovery', async ({
  page,
}) => {
  await newConversation(page);
  await composer(page).fill('Keep this draft.');
  await page.evaluate(() => {
    (
      window as unknown as { __voiceFixture: { denied: boolean } }
    ).__voiceFixture.denied = true;
  });
  await page.getByRole('button', { name: 'Dictate', exact: true }).click();
  await expect(
    page
      .getByRole('alert')
      .filter({ hasText: 'Microphone permission was denied' }),
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Dictate', exact: true }),
  ).toBeEnabled();
  await expect(composer(page)).toHaveValue('Keep this draft.');
  await page.evaluate(() => {
    (
      window as unknown as { __voiceFixture: { denied: boolean } }
    ).__voiceFixture.denied = false;
  });
  await page.getByRole('button', { name: 'Dictate', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Finish dictation', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Cancel dictation', exact: true })
    .click();
  await expect(
    page.getByRole('button', { name: 'Dictate', exact: true }),
  ).toBeEnabled();
});
