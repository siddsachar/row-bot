import { createContext } from 'react';
import type { DeliveryView, ExportSaved } from '../../api/types';
import type { Tone } from '../../ui/primitives';

/**
 * Files Row-Bot sent from a conversation (F7): to the person's own devices,
 * where every signed-in device downloads them from the conversation for a
 * week, or to a chat app. The card reads one delivery when it shows; nothing
 * about it changes on its own except its download running out.
 */
export type DeliveryIo = {
  load: (deliveryId: string, signal: AbortSignal) => Promise<DeliveryView>;
  /** One sent file's bytes, with this device's session. */
  download: (
    deliveryId: string,
    index: number,
    signal?: AbortSignal,
  ) => Promise<Blob>;
  /** Desktop window (it can't download): saved into Exports on this computer. */
  saveToExports: (deliveryId: string, index: number) => Promise<ExportSaved>;
  /** Show a file saved into Exports in the file manager; false if it couldn't. */
  reveal: (fileName: string) => Promise<boolean>;
  /** "Send to your devices instead": the person's own click, no approval. */
  sendToDevices: (deliveryId: string) => Promise<DeliveryView>;
};

export const DeliveryIoContext = createContext<DeliveryIo | null>(null);

const DAY_MS = 24 * 60 * 60 * 1000;

/** Sent to the devices, and its downloads have run out (by the server or this device's clock). */
export function deliveryExpired(view: DeliveryView, now = Date.now()) {
  if (view.status === 'expired') return true;
  if (view.target !== 'devices' || !view.expires_at) return false;
  const end = Date.parse(view.expires_at);
  return Number.isFinite(end) && end <= now;
}

/** "Your Telegram chat" from "your Telegram chat". */
function capitalized(text: string) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

function appName(view: DeliveryView) {
  return view.channel_label || 'the chat app';
}

/** "50 MB". */
export function limitWords(bytes: number) {
  return bytes >= 1024 * 1024
    ? `${Math.round(bytes / (1024 * 1024))} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** What the card is called: where the files went, or didn't. */
export function deliveryTitle(view: DeliveryView, now = Date.now()) {
  const sent = view.files.filter((file) => file.sent).length;
  if (view.target === 'devices')
    return sent || deliveryExpired(view, now)
      ? 'Sent to your devices'
      : 'Not sent to your devices';
  return sent ? `Sent to ${appName(view)}` : `Not sent to ${appName(view)}`;
}

/** How many days the downloads have left: "7 days left", "Less than a day left". */
export function daysLeft(view: DeliveryView, now = Date.now()) {
  const end = view.expires_at ? Date.parse(view.expires_at) : NaN;
  if (!Number.isFinite(end)) return '';
  if (end - now < DAY_MS) return 'Less than a day left';
  const days = Math.round((end - now) / DAY_MS);
  return `${days} day${days === 1 ? '' : 's'} left`;
}

/** Its state in a few words, with a tone. */
export function deliveryStatus(
  view: DeliveryView,
  now = Date.now(),
): { tone: Tone; label: string } {
  const total = view.files.length;
  const sent = view.files.filter((file) => file.sent).length;
  const app = appName(view);
  if (view.target === 'devices') {
    if (deliveryExpired(view, now))
      return { tone: 'neutral', label: 'Expired' };
    if (!sent)
      return {
        tone: 'danger',
        label:
          view.problem === 'too_large' && view.limit_bytes
            ? `Too big for your devices (${limitWords(view.limit_bytes)} limit)`
            : view.problem === 'store_full'
              ? 'No room for more right now'
              : 'Not sent',
      };
    if (sent < total)
      return { tone: 'warning', label: `${sent} of ${total} sent` };
    return { tone: 'success', label: 'Ready to download' };
  }
  if (sent === total) return { tone: 'success', label: 'Sent' };
  if (sent) return { tone: 'warning', label: `${sent} of ${total} sent` };
  switch (view.problem) {
    case 'too_large':
      return {
        tone: 'danger',
        label: view.limit_bytes
          ? `Too big for ${app} (${limitWords(view.limit_bytes)} limit)`
          : `Too big for ${app}`,
      };
    case 'cant_send_files':
      return { tone: 'danger', label: `${app} can't send files` };
    case 'not_running':
      return { tone: 'danger', label: `${app} isn't connected` };
    case 'not_linked':
      return { tone: 'danger', label: `${app} doesn't know your chat yet` };
    default:
      return { tone: 'danger', label: `${app} didn't take it` };
  }
}

/** Where it went, in words, beside its state. */
export function deliveryPlace(view: DeliveryView, now = Date.now()) {
  if (view.target === 'devices') {
    if (deliveryExpired(view, now)) return '';
    return view.files.some((file) => file.sent)
      ? `On all your signed-in devices · ${daysLeft(view, now)}`
      : '';
  }
  return view.files.some((file) => file.sent) && view.place
    ? capitalized(view.place)
    : '';
}

/** What to do, when the state alone doesn't say it. */
export function deliveryAdvice(view: DeliveryView, now = Date.now()) {
  const app = appName(view);
  if (deliveryExpired(view, now))
    return 'Downloads end 7 days after sending. Ask Row-Bot to send it again.';
  if (view.status === 'sent') return '';
  switch (view.problem) {
    case 'too_large':
    case 'cant_send_files':
      return '';
    case 'not_running':
      return `Start ${app} in Settings › Channels, or send to your devices instead.`;
    case 'not_linked':
      return `Set your user for ${app} in Settings › Channels, or message Row-Bot there once.`;
    default:
      return view.detail;
  }
}

/** The primary way out of a send that didn't go to a chat app. */
export function sendInsteadLabel(view: DeliveryView) {
  return view.files.some((file) => file.sent)
    ? 'Send the rest to your devices'
    : 'Send to your devices instead';
}
