import type { ReactNode } from 'react';
import type { SettingsSnapshot } from '../../api/types';
import { Button, Field, Select } from '../../ui/primitives';
import { useTheme } from '../../ui/theme';
import { useOverlay } from '../../ui/overlays';
import type { Accent, Appearance } from '../../ui/theme-model';
import { useWorkspaceActions } from '../shell/workspace-actions';
import {
  PreferencesSnapshotPanel,
  type SettingsMutationIO,
} from './SettingsSnapshotPanels';

export default function Preferences({
  onReset,
  snapshot,
  mutation,
  snapshotState,
}: {
  onReset?: () => void;
  snapshot?: SettingsSnapshot['preferences'];
  mutation?: SettingsMutationIO | null;
  snapshotState?: ReactNode;
}) {
  const { preference, update } = useTheme();
  const { open, notify } = useOverlay();
  const workspaceActions = useWorkspaceActions();
  const reset = onReset ?? workspaceActions?.resetLayout;
  return (
    <div className="stack settings-preferences">
      {snapshot && mutation ? (
        <PreferencesSnapshotPanel snapshot={snapshot} mutation={mutation} />
      ) : (
        snapshotState
      )}
      <details className="settings-supplemental-disclosure">
        <summary>
          <span>
            <strong>Local client controls</strong>
            <small>Appearance and workspace layout on this device</small>
          </span>
        </summary>
        <div className="stack settings-supplemental-content">
          <section
            className="settings-section stack"
            aria-labelledby="appearance-heading"
          >
            <div className="section-heading">
              <div>
                <h3 id="appearance-heading">Local client appearance</h3>
                <p>Choose how this client looks on this device.</p>
              </div>
            </div>
            <div className="field-row">
              <Field label="Appearance">
                <Select
                  value={preference.appearance}
                  onChange={(event) =>
                    update({ appearance: event.target.value as Appearance })
                  }
                >
                  <option value="system">System</option>
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </Select>
              </Field>
              <Field label="Colour theme">
                <Select
                  value={preference.accent}
                  onChange={(event) =>
                    update({ accent: event.target.value as Accent })
                  }
                >
                  <option value="blue">Blue</option>
                  <option value="teal">Teal</option>
                  <option value="violet">Violet</option>
                  <option value="amber">Amber</option>
                </Select>
              </Field>
              <Field
                label="Density"
                hint="Touch controls always keep their full size."
              >
                <Select
                  value={preference.density}
                  onChange={(event) =>
                    update({
                      density: event.target.value as 'comfortable' | 'compact',
                    })
                  }
                >
                  <option value="comfortable">Comfortable</option>
                  <option value="compact">Compact</option>
                </Select>
              </Field>
            </div>
            <label className="check-field">
              <input
                type="checkbox"
                checked={preference.reduce_transparency}
                onChange={(event) =>
                  update({ reduce_transparency: event.target.checked })
                }
              />
              Reduce transparency
            </label>
          </section>
          <section
            className="settings-section stack"
            aria-labelledby="layout-heading"
          >
            <div className="section-heading">
              <div>
                <h3 id="layout-heading">Local client workspace layout</h3>
                <p>
                  Restore panel sizes without changing conversations or
                  appearance.
                </p>
              </div>
            </div>
            {reset ? (
              <Button
                onClick={() =>
                  open({
                    kind: 'alert',
                    title: 'Reset layout?',
                    description:
                      'Restore the default panel sizes and close panels. Your conversations and appearance stay saved.',
                    confirmLabel: 'Reset layout',
                    onConfirm: () => {
                      reset();
                      notify('Layout reset');
                    },
                  })
                }
              >
                Review layout reset
              </Button>
            ) : (
              <p className="muted">
                Layout reset is available from the workspace shell.
              </p>
            )}
          </section>
        </div>
      </details>
    </div>
  );
}
