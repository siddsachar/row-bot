import type { CSSProperties } from 'react';
import { ArrowUp, Check, RotateCcw } from 'lucide-react';
import { Button, Field, Select, Toggle } from '../../ui/primitives';
import { useResolvedTheme, useTheme } from '../../ui/theme';
import { useOverlay } from '../../ui/overlays';
import { TOKENS, type Accent, type Appearance } from '../../ui/theme-model';
import { useWorkspaceActions } from '../shell/workspace-actions';
import { SettingsSection, SettingsSummary, SummaryChip } from './anatomy';

const appearanceLabels: Record<Appearance, string> = {
  system: 'System',
  light: 'Light',
  dark: 'Dark',
};
const accentLabels: Record<Accent, string> = {
  blue: 'Blue',
  teal: 'Teal',
  violet: 'Violet',
  amber: 'Amber',
};

/** Palette variables for one theme, scoped to an element. */
function previewVariables(mode: 'light' | 'dark', accent: Accent) {
  const tokens = TOKENS[mode];
  const style: Record<string, string> = {};
  for (const [name, value] of Object.entries(tokens))
    style[`--${name}`] = value;
  style['--accent-solid'] = TOKENS.accents[accent][mode];
  style['--accent-on-solid'] = tokens['text-inverse'];
  style['--text-link'] = TOKENS.accents[accent][mode];
  style.colorScheme = mode;
  return style as CSSProperties;
}

function MiniApp({
  mode,
  accent,
  density,
  opaque,
}: {
  mode: 'light' | 'dark';
  accent: Accent;
  density: 'compact' | 'comfortable';
  opaque: boolean;
}) {
  return (
    <div
      className="appearance-mini"
      aria-hidden
      data-density={density}
      data-opaque={opaque ? 'true' : undefined}
      style={previewVariables(mode, accent)}
    >
      <div className="appearance-mini-sidebar">
        <span className="appearance-mini-logo" />
        <span className="appearance-mini-nav is-current" />
        <span className="appearance-mini-nav" />
        <span className="appearance-mini-label" />
        <span className="appearance-mini-nav" />
        <span className="appearance-mini-nav short" />
        <span className="appearance-mini-nav" />
      </div>
      <div className="appearance-mini-chat">
        <span className="appearance-mini-bubble">Plan a weekend in Lisbon</span>
        <span className="appearance-mini-answer">
          Here is a relaxed two-day plan with{' '}
          <span className="appearance-mini-link">three neighbourhoods</span> to
          walk.
        </span>
        <span className="appearance-mini-step">
          <Check size={11} aria-hidden /> Searched the web · 3 sources
        </span>
        <span className="appearance-mini-composer">
          <span>Ask anything…</span>
          <span className="appearance-mini-send">
            <ArrowUp size={11} aria-hidden />
          </span>
        </span>
      </div>
      <div className="appearance-mini-context">
        <span className="appearance-mini-label" />
        <span className="appearance-mini-card" />
        <span className="appearance-mini-switch" />
      </div>
    </div>
  );
}

export default function AppearanceSettings({
  onReset,
}: {
  onReset?: () => void;
}) {
  const { preference, update } = useTheme();
  const resolved = useResolvedTheme();
  const { notify } = useOverlay();
  const workspaceActions = useWorkspaceActions();
  const reset = onReset ?? workspaceActions?.resetLayout;
  const opaque = preference.reduce_transparency;
  return (
    <div className="stack settings-appearance">
      <SettingsSummary>
        <SummaryChip>
          {preference.appearance === 'system'
            ? `System · ${resolved === 'dark' ? 'Dark' : 'Light'} now`
            : appearanceLabels[preference.appearance]}
        </SummaryChip>
        <SummaryChip>This device</SummaryChip>
      </SettingsSummary>
      <figure
        className="appearance-preview"
        aria-label={`Preview: ${appearanceLabels[preference.appearance]} appearance, ${accentLabels[preference.accent]} accent, ${preference.density} density`}
      >
        {preference.appearance === 'system' ? (
          <div className="appearance-preview-pair">
            <MiniApp
              mode="light"
              accent={preference.accent}
              density={preference.density}
              opaque={opaque}
            />
            <MiniApp
              mode="dark"
              accent={preference.accent}
              density={preference.density}
              opaque={opaque}
            />
          </div>
        ) : (
          <MiniApp
            mode={preference.appearance}
            accent={preference.accent}
            density={preference.density}
            opaque={opaque}
          />
        )}
        <figcaption>
          {preference.appearance === 'system'
            ? 'Follows your device: light by day, dark when your system switches.'
            : 'Changes apply immediately on this device.'}
        </figcaption>
      </figure>
      <SettingsSection title="Look" anchor="theme">
        <Field
          label="Appearance"
          hint="System follows your device setting."
          layout="row"
        >
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
        <div data-setting-anchor="accent">
          <Field
            label="Colour theme"
            hint="The accent for links, selection and primary actions."
            layout="row"
          >
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
        </div>
        <div data-setting-anchor="density">
          <Field
            label="Density"
            hint="Touch controls always keep their full size."
            layout="row"
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
        <div data-setting-anchor="transparency">
          <Field
            label="Reduce transparency"
            hint="Solid surfaces instead of frosted glass."
            layout="row"
          >
            <Toggle
              label="Reduce transparency"
              checked={preference.reduce_transparency}
              onChange={(event) =>
                update({ reduce_transparency: event.target.checked })
              }
            />
          </Field>
        </div>
      </SettingsSection>
      <SettingsSection title="Workspace layout" anchor="layout">
        <div className="settings-inline-row">
          <div>
            <strong>Panel sizes</strong>
            <p>Restore panel sizes without changing conversations or look.</p>
          </div>
          {reset ? (
            <Button
              onClick={() => {
                reset();
                notify('Layout reset');
              }}
            >
              <RotateCcw size={16} aria-hidden />
              Reset layout
            </Button>
          ) : (
            <p className="muted">
              Layout reset is available from the workspace shell.
            </p>
          )}
        </div>
      </SettingsSection>
    </div>
  );
}
