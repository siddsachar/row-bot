import { useEffect, useState } from 'react';
import { Field, Toggle } from '../../ui/primitives';
import { AppIcon } from '../apps/parts';

export type StepApp = { id: string; name: string; icon: string };

/**
 * The apps a prompt step uses. None chosen: every app the workflow may use. Choosing some limits the
 * step to them, as an @mention does in chat; the workflow's profile and every approval still apply.
 */
export default function StepApps({
  load,
  value,
  onChange,
}: {
  load: (signal?: AbortSignal) => Promise<readonly StepApp[]>;
  value: readonly string[] | null;
  onChange: (next: string[] | null) => void;
}) {
  const [apps, setApps] = useState<readonly StepApp[]>([]);
  useEffect(() => {
    const abort = new AbortController();
    load(abort.signal).then(
      (found) => !abort.signal.aborted && setApps(found),
      () => undefined,
    );
    return () => abort.abort();
  }, [load]);
  const chosen = new Set(value ?? []);
  // A step saved with an app that is gone keeps it listed, so saving never drops it silently.
  const known = new Set(apps.map((app) => app.id));
  const gone = [...chosen].filter((id) => !known.has(id));
  if (!apps.length && !gone.length) return null;
  const set = (id: string, on: boolean) => {
    const next = on
      ? [...chosen, id].slice(0, 8)
      : [...chosen].filter((item) => item !== id);
    onChange(next.length ? next : null);
  };
  return (
    <Field
      label="Apps this step uses"
      hint="None chosen: every app this workflow may use. Approvals still apply."
    >
      <ul className="step-apps">
        {apps.map((app) => (
          <li key={app.id}>
            <AppIcon icon={app.icon} size={16} />
            <Toggle
              label={`Use ${app.name} in this step`}
              checked={chosen.has(app.id)}
              disabled={!chosen.has(app.id) && chosen.size >= 8}
              onChange={(event) => set(app.id, event.target.checked)}
            />
            <span>{app.name}</span>
          </li>
        ))}
        {gone.map((id) => (
          <li key={id}>
            <Toggle
              label={`Use ${id} in this step (no longer set up)`}
              checked
              onChange={() => set(id, false)}
            />
            <span>{id} (no longer set up)</span>
          </li>
        ))}
      </ul>
    </Field>
  );
}
