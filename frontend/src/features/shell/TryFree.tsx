import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ImageOff, Loader2, MessagesSquare, Eye } from 'lucide-react';
import type { OpenRouterSignIn } from '../../api/types';
import { clientError } from '../../api/errors';
import { usePlace } from '../../api/place';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';

/**
 * OpenRouter's free models in the order first run offers them: a few that use
 * tools well first, then OpenRouter's own router to whichever free model is up.
 * The free list changes often; any other free model follows these.
 */
export const FREE_MODEL_ORDER = [
  'nvidia/nemotron-3-ultra-550b-a55b:free',
  'google/gemma-4-31b-it:free',
  'openrouter/free',
];

export function isFreeModel(modelId: string) {
  return modelId.endsWith(':free') || modelId === 'openrouter/free';
}

/** Free models, recommended first. */
export function freeFirst<T extends { model_id: string }>(rows: T[]): T[] {
  const rank = (row: T) => {
    const at = FREE_MODEL_ORDER.indexOf(row.model_id);
    return at < 0 ? FREE_MODEL_ORDER.length : at;
  };
  return rows
    .filter((row) => isFreeModel(row.model_id))
    .sort((a, b) => rank(a) - rank(b));
}

const FACTS = [
  {
    icon: MessagesSquare,
    text: 'About 50 messages a day. Each step of a longer task counts as one.',
  },
  {
    icon: Eye,
    text: 'Your messages go to OpenRouter and the free model’s host. Some free hosts may keep what you send.',
  },
  {
    icon: ImageOff,
    text: 'Free models don’t make pictures. Add an image model any time in Settings › Models.',
  },
];

/**
 * "Try it free": sign in to OpenRouter (an account is made if needed), then
 * pick one of its free models. Nothing is sent before Sign in is chosen.
 */
export default function TryFreePanel({
  connected,
  models,
  onConnected,
  onUseKey,
}: {
  /** OpenRouter already has a key: its free models straight away. */
  connected: boolean;
  /** The free models to pick from, once signed in. */
  models: ReactNode;
  onConnected: () => void;
  onUseKey: () => void;
}) {
  const { controller } = useRuntime();
  const place = usePlace();
  const [flow, setFlow] = useState<OpenRouterSignIn | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const tab = useRef<Window | null>(null);

  const waiting = flow?.phase === 'waiting' || flow?.phase === 'finishing';
  useEffect(() => {
    if (!flow || !waiting) return;
    let stopped = false;
    const timer = window.setTimeout(() => {
      void controller
        .openRouterSignIn(flow.flow_id)
        .then((next) => {
          if (stopped || !next) return;
          setFlow(next);
          if (next.phase === 'done') onConnected();
        })
        .catch((cause) => !stopped && setError(clientError(cause).message));
    }, 1500);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [controller, flow, waiting, onConnected]);

  async function signIn() {
    setError('');
    setStarting(true);
    // From another device the page opens here: a tab opened by the click itself
    // isn't blocked. On this computer Row-Bot opens the browser itself.
    tab.current =
      place.kind === 'this_computer' ? null : window.open('', '_blank');
    try {
      const next = await controller.startOpenRouterSignIn();
      setFlow(next);
      if (next.route === 'device' && next.url && tab.current)
        tab.current.location.href = next.url;
      else tab.current?.close();
    } catch (cause) {
      tab.current?.close();
      setError(clientError(cause).message);
    } finally {
      setStarting(false);
    }
  }

  async function cancel() {
    if (!flow) return;
    const next = await controller
      .cancelOpenRouterSignIn(flow.flow_id)
      .catch(() => null);
    setFlow(next);
  }

  if (connected || flow?.phase === 'done') return <>{models}</>;
  const ended =
    flow &&
    ['failed', 'expired', 'unavailable', 'cancelled'].includes(flow.phase);
  return (
    <div className="first-run-panel-body try-free">
      <h3>Try Row-Bot free</h3>
      <p>
        Sign in to OpenRouter, which makes you a free account if you don’t have
        one. Row-Bot then uses its free models.
      </p>
      <ul className="try-free-facts" aria-label="Before you start">
        {FACTS.map(({ icon: Icon, text }) => (
          <li key={text}>
            <Icon size={16} aria-hidden />
            <span>{text}</span>
          </li>
        ))}
      </ul>
      {waiting ? (
        <div className="try-free-waiting">
          <p role="status">
            <Loader2 size={14} className="spin" aria-hidden /> Finish signing in
            on the OpenRouter page.
          </p>
          <div className="actions">
            {flow?.url && (
              <a
                className="button secondary"
                href={flow.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open the page again
              </a>
            )}
            <Button variant="ghost" onClick={() => void cancel()}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          {(error || (ended && flow?.message)) && (
            <p role="alert">{error || flow?.message}</p>
          )}
          <div className="actions">
            <Button
              variant="primary"
              disabled={starting}
              onClick={() => void signIn()}
            >
              Sign in with OpenRouter
            </Button>
            <span className="try-free-key">
              Have a key?{' '}
              <button type="button" onClick={onUseKey}>
                Use an API key
              </button>
            </span>
          </div>
        </>
      )}
    </div>
  );
}
