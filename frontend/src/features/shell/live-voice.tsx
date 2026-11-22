/**
 * Live voice in a chat's Talk panel: the disclosure said before a provider's
 * first use, and what Row-Bot asks the front to say from a run's view.
 */
import { Link } from 'react-router-dom';
import type { LiveVoiceProvider, VoiceRunView } from '../../api/types';
import type { RealtimeOutput } from '../../api/voice_realtime';
import { Button } from '../../ui/primitives';

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

/** Who hears the audio and what it costs, said before Live voice is first used. */
export function liveVoiceDisclosure(provider: LiveVoiceProvider): string {
  return `Your voice and this chat’s recent messages go to ${provider.company} while you talk. ${provider.price.charAt(0).toUpperCase()}${provider.price.slice(1)}, from ${provider.company}’s published prices (${provider.price_checked}), billed to your ${provider.company} key.`;
}

/** What the person hears: the consult's answer once its run ends, and its progress cues meanwhile. */
export function liveOutputs(
  run: VoiceRunView | null,
  answered: Map<string, string>,
): RealtimeOutput[] {
  if (!run?.run_id) return [];
  const runId = run.run_id;
  const outputs: RealtimeOutput[] = (run.cues ?? []).map((cue) => ({
    id: `${runId}:${cue.id}`,
    runId,
    kind: 'cue',
    text: cue.text,
  }));
  if (run.call_id && run.function_output) {
    // The call is answered once; a later answer (after an approval) is spoken as Row-Bot's.
    const id = `${runId}:${run.state}`;
    const answer = answered.get(run.call_id);
    outputs.push(
      !answer || answer === id
        ? {
            id,
            runId,
            kind: 'function_output',
            callId: run.call_id,
            text: run.function_output,
          }
        : { id, runId, kind: 'final', text: run.text ?? '' },
    );
  } else if (!run.call_id && run.output_id && run.text) {
    outputs.push({ id: run.output_id, runId, kind: 'final', text: run.text });
  }
  return outputs.filter((output) => output.text);
}

export function LiveVoiceDisclosure(props: {
  provider: LiveVoiceProvider;
  acknowledged: boolean;
  onAcknowledge(): void;
}) {
  const provider = props.provider;
  return (
    <div className="voice-live-disclosure" role="note">
      <p>{liveVoiceDisclosure(provider)}</p>
      {!provider.available ? (
        <p className="voice-state-reason">
          Add your {provider.company} API key in{' '}
          <Link to="/settings/models">Providers</Link> to use it.
        </p>
      ) : (
        !props.acknowledged &&
        !liveVoiceDisclosed(provider.id) && (
          <Button
            variant="secondary"
            onClick={() => {
              remember(provider.id);
              props.onAcknowledge();
            }}
          >
            Use {provider.label}
          </Button>
        )
      )}
    </div>
  );
}
