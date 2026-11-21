import { useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import { GraduationCap, Lock, Settings } from 'lucide-react';
import { Button, IconButton } from '../../ui/primitives';
import type { LearningIO } from './learning-state';
import { tokenWords } from './words';
import './learning.css';

/** What finding and testing fixes costs, in one clause, or '' when Learning makes no model calls. */
function costWords(model: string, limit: number | null): string {
  const uses = model ? `${model} (your default model)` : 'your default model';
  if (limit === null) return `${uses}, with no monthly limit`;
  if (limit <= 0) return '';
  return `${uses}, up to ${tokenWords(limit)} tokens a month`;
}

/**
 * Home's one-time note that Learning is on (O195). Learning is on unless the
 * person turns it off, so they hear once what it does, what it costs and where
 * to change it. Got it ends the note on every device; so does turning Learning
 * on in Settings.
 */
export function LearningIntro({ io }: { io: LearningIO | null }) {
  const navigate = useNavigate();
  const [closed, setClosed] = useState(false);
  const snapshot = io?.snapshot;
  if (!io || !snapshot?.introduce || closed) return null;
  const close = () => {
    setClosed(true);
    // Not saved (offline, say): the note comes back next time, nothing worse.
    void io.run('close_intro').catch(() => undefined);
  };
  const cost = costWords(
    snapshot.spending.model_name,
    snapshot.spending.limit_tokens,
  );
  return (
    <div
      className="overview-card overview-cell learning-intro"
      role="region"
      aria-labelledby="learning-intro-title"
      style={{ '--overview-order': 1 } as CSSProperties}
    >
      <span className="overview-tile" data-tone="accent" aria-hidden>
        <GraduationCap size={16} />
      </span>
      <span className="overview-row-text">
        <strong className="overview-row-title" id="learning-intro-title">
          Row-Bot learns from your corrections
        </strong>
        <span className="overview-row-meta">
          When you correct a reply, or stop or retry one, Row-Bot works out a
          fix during quiet hours, tests it, and asks you before using it.
        </span>
      </span>
      <p className="learning-intro-privacy">
        <Lock size={14} aria-hidden />
        <span>
          What it records stays on this computer.{' '}
          {cost
            ? `Finding and testing fixes uses ${cost}.`
            : 'It makes no model calls.'}
        </span>
      </p>
      <div className="learning-intro-actions">
        <IconButton
          size="sm"
          label="Learning settings"
          onClick={() => {
            close();
            navigate('/settings/learning');
          }}
        >
          <Settings size={15} aria-hidden />
        </IconButton>
        <Button variant="primary" className="small" onClick={close}>
          Got it
        </Button>
      </div>
    </div>
  );
}
