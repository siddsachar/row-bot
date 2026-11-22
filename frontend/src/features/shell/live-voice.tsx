/**
 * Live voice in a chat: the mode menu's rows, and the one-time disclosure
 * said before a provider's first use (or why it can't be used yet).
 */
import { Link } from 'react-router-dom';
import { X } from 'lucide-react';
import type { LiveVoiceProvider } from '../../api/types';
import { Button, IconButton, type MenuAction } from '../../ui/primitives';

export type LiveId = LiveVoiceProvider['id'];
const ACK_KEY = 'row-bot.live-voice.disclosed.';

export function liveVoiceDisclosed(id: LiveId): boolean {
  try {
    return globalThis.localStorage?.getItem(ACK_KEY + id) === '1';
  } catch {
    return false;
  }
}

function remember(id: LiveId) {
  try {
    globalThis.localStorage?.setItem(ACK_KEY + id, '1');
  } catch {
    /* the disclosure shows again next time */
  }
}

function capital(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Everything that goes to the provider, and what it costs, said before Live voice is first used. */
export function liveVoiceDisclosure(
  provider: LiveVoiceProvider,
  incognito = false,
): string {
  // An incognito chat sends no memories (the host builds the context without them).
  const what = incognito
    ? 'Your voice, this chat’s recent messages and the agent profile’s name go'
    : 'Your voice, this chat’s recent messages, the agent profile’s name and a few related memories go';
  return `${what} to ${provider.company} while you talk. ${capital(provider.price)}, from ${provider.company}’s published prices (${provider.price_checked}), billed to your ${provider.company} key.`;
}

/** One mode-menu row per provider: its price, or why it can't be used yet. */
export function liveVoiceActions(
  providers: LiveVoiceProvider[],
  choose: (provider: LiveVoiceProvider) => void,
  disabled: boolean,
): MenuAction[] {
  return providers.map((provider, index) => ({
    label: `Live voice · ${provider.label}`,
    description: provider.available
      ? `${capital(provider.price)} · audio goes to ${provider.company}`
      : `Needs your ${provider.company} API key`,
    // A provider without a key still opens, to say where the key goes.
    disabled: disabled && provider.available,
    separatorBefore: index === 0,
    onSelect: () => choose(provider),
  }));
}

/** The panel shown before a provider's first use, or when it has no key. */
export function LiveVoiceDisclosure(props: {
  provider: LiveVoiceProvider;
  incognito?: boolean;
  disabled?: boolean;
  onStart(): void;
  onClose(): void;
}) {
  const provider = props.provider;
  return (
    <section
      aria-label="Live voice"
      className="surface conversation-voice-surface voice-live-disclosure"
    >
      <div className="voice-live-disclosure-head">
        <strong>Live voice · {provider.label}</strong>
        <IconButton size="sm" label="Close" onClick={props.onClose}>
          <X size={16} aria-hidden />
        </IconButton>
      </div>
      <p role="note">{liveVoiceDisclosure(provider, props.incognito)}</p>
      {provider.available ? (
        <Button
          variant="primary"
          disabled={props.disabled}
          onClick={() => {
            remember(provider.id);
            props.onStart();
          }}
        >
          Start {provider.label}
        </Button>
      ) : (
        <p className="voice-state-reason">
          Add your {provider.company} API key in{' '}
          <Link to="/settings/models#providers-api">Providers</Link> to use it.
        </p>
      )}
    </section>
  );
}
