import { useEffect, useState } from 'react';
import type { CompanyView, ModelChoice } from '../../api/types';
import { useOptionalRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';
import { splitModelLabel } from '../shell/model-choices';
import { COMPANY_PROVIDER } from './words';

/**
 * The model picker, before a model is chosen: the model the company suggests
 * (Row-Bot for Teams, C2), one press to use it. Nothing is chosen for the
 * person. Only a picker that lists the company's models reads the company's
 * state (which contacts nothing); any other picker asks nothing.
 */
export default function CompanySuggestion({
  models,
  onChoose,
}: {
  models: readonly ModelChoice[];
  onChoose: (model: ModelChoice) => void;
}) {
  const controller = useOptionalRuntime()?.controller;
  const offered = models.some(
    (model) => model.provider_id === COMPANY_PROVIDER && model.available,
  );
  const [view, setView] = useState<CompanyView | null>(null);

  useEffect(() => {
    if (!offered || !controller) return;
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
  }, [controller, offered]);

  const suggested = view?.suggested_model_ref
    ? models.find(
        (model) =>
          model.model_ref === view.suggested_model_ref && model.available,
      )
    : undefined;
  if (!view || !suggested) return null;
  const name = splitModelLabel(suggested.label).name;
  return (
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
  );
}
