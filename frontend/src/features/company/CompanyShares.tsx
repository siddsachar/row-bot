import { useEffect, useState } from 'react';
import { Building2, RefreshCw, Undo2 } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type {
  CompanyLock as CompanyLockView,
  CompanyShare,
  CompanySharesView,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useOverlay } from '../../ui/overlays';
import { EntityList, EntityRow, IconButton } from '../../ui/primitives';
import { SettingsGroup, SettingsItem } from '../settings/anatomy';
import CompanyLock from './CompanyLock';
import {
  WITHDRAW_WORDS,
  disconnectedWords,
  lockDetail,
  lockdownWords,
  shareStatus,
  unreachableWords,
} from './words';

export type CompanySharesController = Pick<
  ClientController,
  'companyShares' | 'withdrawCompanyShare'
>;

/** "Shared 24 Nov 2026" in this device's way of writing dates. */
function sharedWords(at: string): string {
  const date = new Date(at);
  return Number.isNaN(date.getTime())
    ? 'Shared'
    : `Shared ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date)}`;
}

/**
 * Settings › Company's Your shares (Row-Bot for Teams, B4b): what the person
 * shared from chats, and what became of each: waiting for review, approved
 * (a fact in its space, perhaps moved to another by a reviewer) or rejected
 * with the reviewer's reason. A waiting one can be withdrawn, which deletes
 * it. While the company turns sharing off, the lock says so.
 */
export default function CompanyShares({
  controller,
  lock,
}: {
  controller: CompanySharesController;
  /** "Set by your company" for Share to company, when the company turned it off. */
  lock?: CompanyLockView;
}) {
  const overlay = useOverlay();
  const [view, setView] = useState<CompanySharesView | null>(null);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; alert: boolean } | null>(
    null,
  );

  useEffect(() => {
    let current = true;
    const read = async () => {
      try {
        const next = await controller.companyShares();
        if (current) setView(next);
      } catch {
        // Outside the app, or its read failed: the group stays away.
      }
    };
    void read();
    return () => {
      current = false;
    };
  }, [controller, reload]);

  if (!view || view.state === 'not_joined') return null;
  const company = view.company ?? 'your company';

  async function withdraw(share: CompanyShare) {
    setBusy(true);
    try {
      const next = await controller.withdrawCompanyShare(share.id);
      setView(next);
      setNotice(
        next.state !== 'ready'
          ? null
          : next.outcome === 'withdrawn'
            ? { text: `“${share.title}” was withdrawn.`, alert: false }
            : { text: next.words ?? 'It wasn’t withdrawn.', alert: true },
      );
    } catch (cause) {
      setNotice({ text: clientError(cause).message, alert: true });
    } finally {
      setBusy(false);
    }
  }

  function confirmWithdraw(share: CompanyShare) {
    overlay.open({
      kind: 'alert',
      title: `Withdraw “${share.title}”?`,
      description: WITHDRAW_WORDS,
      confirmLabel: 'Withdraw',
      cancelLabel: 'Keep it',
      onConfirm: () => void withdraw(share),
    });
  }

  const ready = view.state === 'ready';
  return (
    <SettingsGroup
      title="Your shares"
      anchor="your-shares"
      className="company-shares"
      note={`What you shared from chats with ${company}. Each space’s owners review a share before anyone else sees it.`}
    >
      {lock && (
        <SettingsItem
          label="Share to company"
          help={lock.words}
          icon={<Building2 size={16} aria-hidden />}
          tone="neutral"
          bind={false}
          status={<CompanyLock detail={lockDetail(lock)} />}
        />
      )}
      {notice && (
        <p
          role={notice.alert ? 'alert' : 'status'}
          className="settings-divided company-documents-notice"
        >
          {notice.text}
        </p>
      )}
      {!ready && (
        <div className="settings-divided company-documents-state">
          <p>
            {view.state === 'disconnected'
              ? disconnectedWords(company)
              : view.state === 'locked_down'
                ? lockdownWords({ company, policy_reason: null })
                : unreachableWords(company, view.reason)}
          </p>
          {view.state !== 'disconnected' && (
            <IconButton
              size="sm"
              label={view.state === 'locked_down' ? 'Retry' : 'Try again'}
              onClick={() => setReload((value) => value + 1)}
            >
              <RefreshCw size={15} aria-hidden />
            </IconButton>
          )}
        </div>
      )}
      {ready && view.shares.length === 0 && (
        <p className="settings-divided muted">
          {lock
            ? 'Nothing shared yet.'
            : 'Nothing shared yet. Use Share to company on a reply in a chat.'}
        </p>
      )}
      {ready && view.shares.length > 0 && (
        <EntityList label="Your shares">
          {view.shares.map((share) => {
            const status = shareStatus(share);
            return (
              <EntityRow
                key={share.id}
                title={share.title}
                status={{ tone: status.tone, label: status.label }}
                meta={[
                  share.status === 'pending' ? share.space_name : null,
                  status.detail,
                  sharedWords(share.submitted_at),
                ]
                  .filter(Boolean)
                  .join(' · ')}
                action={
                  share.status === 'pending' ? (
                    <IconButton
                      size="sm"
                      label={`Withdraw “${share.title}”`}
                      disabled={busy}
                      onClick={() => confirmWithdraw(share)}
                    >
                      <Undo2 size={15} aria-hidden />
                    </IconButton>
                  ) : undefined
                }
              />
            );
          })}
        </EntityList>
      )}
      {ready && view.truncated && (
        <p className="settings-divided muted">Only the latest 200 are shown.</p>
      )}
    </SettingsGroup>
  );
}
