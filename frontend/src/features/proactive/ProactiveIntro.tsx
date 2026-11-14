import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Lock, Settings, X } from 'lucide-react';
import type { ProactiveSettingsView } from '../../api/types';
import { Button, IconButton, Segmented } from '../../ui/primitives';
import { useNotify } from '../../ui/overlays';
import { PHONE_LAYOUT, useMedia } from '../../ui/use-media';
import type { ProactiveStore } from './proactive-state';
import { LEVEL_LINES, PRIVACY_LINE, type ProactiveLevel } from './words';

const INTRODUCED_KEY = 'row-bot:proactive-introduced:v1';

/** Whether the introduction was answered or dismissed on this device. */
export function introduced(): boolean {
  try {
    return window.localStorage.getItem(INTRODUCED_KEY) === '1';
  } catch {
    return true;
  }
}

function markIntroduced() {
  try {
    window.localStorage.setItem(INTRODUCED_KEY, '1');
  } catch {
    // Private mode: it may show again next time, nothing worse.
  }
}

/**
 * Proactive's one-time introduction on Home, after setup: what it does, the
 * level (already Quiet, shown selected; a change applies at once) and the
 * honest privacy line. Never asks to connect anything.
 */
export function ProactiveIntro({
  store,
  settings,
  onDone,
}: {
  store: ProactiveStore;
  settings: ProactiveSettingsView;
  onDone(): void;
}) {
  const navigate = useNavigate();
  const notify = useNotify();
  const phone = useMedia(PHONE_LAYOUT);
  const [level, setLevel] = useState<ProactiveLevel>(settings.level);
  async function choose(next: ProactiveLevel) {
    const before = level;
    setLevel(next);
    try {
      await store.settingsChanged(() =>
        store.io.changeSettings({ level: next }),
      );
    } catch {
      setLevel(before);
      notify('Couldn’t save. Try again.', 'danger');
    }
  }
  const done = () => {
    markIntroduced();
    onDone();
  };
  return (
    <div
      className="overview-card proactive-intro"
      role="region"
      aria-labelledby="proactive-intro-title"
    >
      <div className="proactive-intro-head">
        <h4 id="proactive-intro-title">Row-Bot can speak first</h4>
        <IconButton size="sm" label="Close" onClick={done}>
          <X size={15} aria-hidden />
        </IconButton>
      </div>
      <p>
        When something’s worth your attention, like a reply you’re waiting on or
        a meeting about to start, Row-Bot gets it ready and tells you at a good
        moment. It never sends or changes anything without you.
      </p>
      <Segmented
        label="How much Row-Bot brings up"
        size={phone ? 'sm' : 'md'}
        value={level}
        onChange={(next) => void choose(next)}
        options={settings.levels.map((item) => ({
          value: item.id,
          label: item.label,
        }))}
      />
      <p className="proactive-level-line" aria-live="polite">
        {LEVEL_LINES[level].line}
      </p>
      <p className="proactive-privacy">
        <Lock size={14} aria-hidden />
        <span>{PRIVACY_LINE}</span>
      </p>
      <div className="proactive-intro-actions">
        <IconButton
          size="sm"
          label="Proactive settings"
          onClick={() => {
            done();
            navigate('/settings/proactive');
          }}
        >
          <Settings size={15} aria-hidden />
        </IconButton>
        <Button variant="primary" className="small" onClick={done}>
          Done
        </Button>
      </div>
    </div>
  );
}
