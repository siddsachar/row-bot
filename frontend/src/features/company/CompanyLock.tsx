import { Lock } from 'lucide-react';
import { Hint } from '../../ui/primitives';

/**
 * "Set by your company" (Row-Bot for Teams, C3a): a lock and the words, with
 * the setting in plain words in its tooltip ("Allow personal API keys: Off").
 * A row the company's policy controls shows it in place of its control, so
 * there is nothing to change there. Keyboard focus opens the tooltip; screen
 * readers hear the setting with the words.
 */
export default function CompanyLock({ detail }: { detail: string }) {
  return (
    <Hint label={detail}>
      <span className="settings-row-status-line" tabIndex={0}>
        <Lock size={14} aria-hidden />
        Set by your company
        <span className="visually-hidden">: {detail}</span>
      </span>
    </Hint>
  );
}
