import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import type { CompanyView, ModelChoice } from '../../api/types';
import { useOptionalRuntime } from '../../runtime';
import { Button, IconButton } from '../../ui/primitives';
import { splitModelLabel } from '../shell/model-choices';
import { COMPANY_PROVIDER, lockdownWords } from './words';

/**
 * The model picker's company line (Row-Bot for Teams):
 * - while no model is chosen, or the chosen one can't be used (the company's
 *   policy took it away, say), the model the company suggests, one press to
 *   use it (C2). Nothing is chosen for the person.
 * - while locked down (C3a), one calm line saying Row-Bot uses only what's
 *   safe on this computer, with a Retry.
 * Only a picker that lists the company's models reads the company's state
 * (which contacts nothing); any other picker asks nothing.
 */
export default function CompanySuggestion({
  models,
  suggest,
  onChoose,
}: {
  models: readonly ModelChoice[];
  /** Offer the company's suggestion (no model chosen, or it can't be used). */
  suggest: boolean;
  onChoose: (model: ModelChoice) => void;
}) {
  const controller = useOptionalRuntime()?.controller;
  const listed = models.some((model) => model.provider_id === COMPANY_PROVIDER);
  const [view, setView] = useState<CompanyView | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!listed || !controller) return;
    let current = true;
    controller.company('status').then(
      (next) => {
        if (current) setView(next);
      },
      () => undefined,
    );
    return () => {
      current = false;
    };
  }, [controller, listed]);

  async function retry() {
    if (!controller) return;
    setBusy(true);
    try {
      setView(await controller.company('check'));
    } catch {
      // The line stays; Retry can be pressed again.
    } finally {
      setBusy(false);
    }
  }

  const lockedDown = view?.policy_state === 'locked_down';
  const suggested =
    suggest && !lockedDown && view?.suggested_model_ref
      ? models.find(
          (model) =>
            model.model_ref === view.suggested_model_ref && model.available,
        )
      : undefined;
  if (!view || (!lockedDown && !suggested)) return null;
  const name = suggested ? splitModelLabel(suggested.label).name : '';
  return (
    <>
      {lockedDown && (
        <div className="model-picker-thinking">
          <p className="model-picker-note">{lockdownWords(view)}</p>
          {view.policy_reason !== 'disconnected' && (
            <IconButton
              label="Retry"
              disabled={busy}
              onClick={() => void retry()}
            >
              <RefreshCw size={14} aria-hidden />
            </IconButton>
          )}
        </div>
      )}
      {suggested && (
        <div className="model-picker-thinking">
          <p className="model-picker-note">
            {view.company ?? 'Your company'} suggests {name}
          </p>
          <Button
            className="small"
            aria-label={`Use ${name}`}
            onClick={() => onChoose(suggested)}
          >
            Use
          </Button>
        </div>
      )}
    </>
  );
}
