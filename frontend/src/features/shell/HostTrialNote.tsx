import { ExternalLink, MemoryStick } from 'lucide-react';
import type { HostTrial } from '../../api/types';
import { webPage } from './hosted-links';

/**
 * A host's trial server (A4): one quiet line saying what works with its
 * memory, and the host's own page to upgrade. The server says when: a known
 * host's server with 1 GB of memory or less.
 */
export default function HostTrialNote({
  trial,
  hostName,
}: {
  trial: HostTrial;
  hostName: string;
}) {
  return (
    <p className="host-trial-note">
      <MemoryStick size={14} aria-hidden />
      <span>
        {trial.note}
        {webPage(trial.upgrade_url) && (
          <>
            {' '}
            <a
              href={trial.upgrade_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              Upgrade in {hostName}
              <ExternalLink size={12} aria-hidden />
            </a>
          </>
        )}
      </span>
    </p>
  );
}
