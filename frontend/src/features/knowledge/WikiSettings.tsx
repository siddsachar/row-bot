import { useEffect, useSyncExternalStore } from 'react';
import type { ClientController } from '../../api';
import { Button, Field, Input } from '../../ui/primitives';

export type WikiAction =
  | 'wiki.configure'
  | 'wiki.publish'
  | 'wiki.rebuild'
  | 'wiki.import'
  | 'wiki.sync';
export interface WikiStatus {
  schema_version: number;
  revision: string;
  enabled: boolean;
  availability: 'available' | 'scope_required' | 'unavailable';
  scope_id: string | null;
  articles: number | null;
  edited: number | null;
  conflicts: number | null;
}
export interface WikiArticle {
  article_id: string;
  entity_id: string | null;
  title: string;
  status:
    | 'unchanged'
    | 'edited'
    | 'conflict'
    | 'legacy_review'
    | 'missing'
    | 'unmanaged';
  vault_hash: string | null;
  db_revision: string | null;
}
export interface WikiPage {
  schema_version: number;
  revision: string;
  scope_id: string;
  items: WikiArticle[];
  total: number;
  next_cursor: string | null;
}
export interface WikiArticleReview {
  article_id: string;
  entity_id: string | null;
  title: string;
  vault_text: string;
  vault_hash: string;
  database_text: string | null;
  db_revision: string | null;
}
export interface WikiReview {
  schema_version: number;
  action: WikiAction;
  scope_id: string;
  revision: string;
  source_revision: string | null;
  vault_revision: string | null;
  articles: WikiArticleReview[];
  enabled: boolean | null;
  action_digest: string;
  review_id: string;
  entity_id?: string | null;
  entity_revision?: string | null;
}

/** Keep the authorized folder grant inside the authenticated owner, never presentation state. */
export function createWikiSettingsSession(controller: ClientController) {
  let folderGrant: string | undefined;
  const requireGrant = () => {
    if (!folderGrant) throw { code: 'folder_selection_required' };
    return folderGrant;
  };
  return new WikiSettingsSession({
    status: (signal) => controller.wikiStatus(folderGrant, signal),
    articles: (cursor, signal) =>
      controller.wikiArticles(requireGrant(), cursor, signal),
    article: (article, signal) =>
      controller.wikiArticle(requireGrant(), article, signal),
    review: (action, payload, signal) =>
      controller.reviewWiki(action, requireGrant(), payload, signal),
    execute: (action, payload, review, commandId) =>
      controller.executeWiki({
        command_id: commandId,
        type: action,
        payload: {
          ...payload,
          review_id: review.review_id,
          folder_grant: requireGrant(),
        },
      }),
    receipt: async (commandId, signal) => {
      try {
        return await controller.wikiReceipt(requireGrant(), commandId, signal);
      } catch (error) {
        if ((error as { code?: string }).code === 'not_found') return null;
        throw error;
      }
    },
    chooseVault: async () => {
      const result = await controller.pickFolder();
      if (result.status === 'selected' && result.grant_id)
        folderGrant = result.grant_id;
    },
  });
}
export interface WikiResult {
  command_id: string;
  status: 'completed' | 'partial';
  action: WikiAction;
  count: number;
  conflicts: number;
  code: string | null;
}
type Payload = {
  revision: string;
  enabled?: boolean;
  entity_id?: string;
  article_ids?: string[];
};
export interface WikiSettingsIO {
  status(signal: AbortSignal): Promise<WikiStatus>;
  articles(cursor: string | undefined, signal: AbortSignal): Promise<WikiPage>;
  article(id: string, signal: AbortSignal): Promise<WikiArticleReview>;
  review(
    action: WikiAction,
    payload: Payload,
    signal: AbortSignal,
  ): Promise<WikiReview>;
  execute(
    action: WikiAction,
    payload: Payload,
    review: WikiReview,
    commandId: string,
  ): Promise<WikiResult>;
  receipt(commandId: string, signal: AbortSignal): Promise<WikiResult | null>;
  chooseVault?(): Promise<void>;
}
interface State {
  status: WikiStatus | null;
  page: WikiPage | null;
  article: WikiArticleReview | null;
  enabled: boolean;
  entityId: string;
  selected: string[];
  trimmed: boolean;
  review: { value: WikiReview; payload: Payload } | null;
  pending: {
    action: WikiAction;
    payload: Payload;
    review: WikiReview;
    commandId: string;
  } | null;
  result: WikiResult | null;
  busy: boolean;
  error: string | null;
}
const initial = (): State => ({
  status: null,
  page: null,
  article: null,
  enabled: false,
  entityId: '',
  selected: [],
  trimmed: false,
  review: null,
  pending: null,
  result: null,
  busy: false,
  error: null,
});
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

/** Inject once per authenticated settings lifetime; disposing purges private text. */
export class WikiSettingsSession {
  private state = initial();
  private listeners = new Set<() => void>();
  private disposed = false;
  private reader: AbortController | null = null;
  constructor(private io: WikiSettingsIO) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private set(patch: Partial<State>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }
  hasRetained = () =>
    !this.disposed &&
    Boolean(
      this.state.pending ||
      this.state.review ||
      this.state.busy ||
      this.state.entityId ||
      (this.state.status && this.state.enabled !== this.state.status.enabled),
    );
  dispose = () => {
    this.reader?.abort();
    this.state = initial();
    this.disposed = true;
    this.listeners.forEach((fn) => fn());
  };
  private async read(work: (signal: AbortSignal) => Promise<void>) {
    if (this.disposed || this.state.busy) return;
    const controller = new AbortController();
    this.reader = controller;
    this.set({ busy: true, error: null });
    try {
      await work(controller.signal);
    } catch {
      if (!controller.signal.aborted)
        this.set({
          error:
            'Wiki state could not be loaded. Reload and review the current versions.',
        });
    } finally {
      if (this.reader === controller) {
        this.reader = null;
        this.set({ busy: false });
      }
    }
  }
  load = async () => {
    if (this.state.pending) return;
    await this.read(async (signal) => {
      const status = await this.io.status(signal);
      const page =
        status.availability === 'available' && status.articles !== null
          ? await this.io.articles(undefined, signal)
          : null;
      if (signal.aborted) return;
      this.set({
        status,
        page,
        enabled: status.enabled,
        selected: [],
        review: null,
        article: null,
        trimmed: false,
      });
    });
  };
  more = async () => {
    const page = this.state.page;
    if (!page?.next_cursor || this.state.pending) return;
    await this.read(async (signal) => {
      const next = await this.io.articles(page.next_cursor!, signal);
      if (signal.aborted) return;
      if (next.revision !== page.revision || next.scope_id !== page.scope_id)
        throw new Error('stale');
      const items = [...page.items, ...next.items];
      this.set({
        page: { ...next, items: items.slice(-200) },
        trimmed: this.state.trimmed || items.length > 200,
      });
    });
  };
  enabled = (enabled: boolean) => {
    if (!this.state.busy && !this.state.pending)
      this.set({ enabled, review: null });
  };
  entityId = (entityId: string) => {
    if (!this.state.busy && !this.state.pending)
      this.set({ entityId, review: null });
  };
  select = (id: string, selected: boolean) => {
    if (this.state.busy || this.state.pending) return;
    const ids = this.state.selected.filter((value) => value !== id);
    if (selected && ids.length < 50) ids.push(id);
    this.set({ selected: ids, review: null });
  };
  open = async (id: string) => {
    await this.read(async (signal) => {
      const article = await this.io.article(id, signal);
      if (!signal.aborted) this.set({ article });
    });
  };
  closeArticle = () => {
    if (!this.state.busy) this.set({ article: null });
  };
  chooseVault = async () => {
    if (!this.io.chooseVault || this.state.pending || this.state.busy) return;
    await this.read(async () => {
      await this.io.chooseVault!();
    });
    await this.load();
  };
  canChooseVault = () => Boolean(this.io.chooseVault);
  review = async (action: WikiAction, articleId?: string) => {
    const status = this.state.status;
    if (!status || this.state.pending) return;
    const payload: Payload = { revision: status.revision };
    if (action === 'wiki.configure') payload.enabled = this.state.enabled;
    if (action === 'wiki.publish') payload.entity_id = this.state.entityId;
    if (action === 'wiki.import') payload.article_ids = [articleId!];
    if (action === 'wiki.sync') payload.article_ids = [...this.state.selected];
    await this.read(async (signal) => {
      const value = await this.io.review(action, clone(payload), signal);
      if (signal.aborted) return;
      if (
        value.action !== action ||
        value.revision !== payload.revision ||
        (status.scope_id && value.scope_id !== status.scope_id)
      )
        throw new Error('Wrong review');
      this.set({
        review: { value: clone(value), payload: clone(payload) },
        result: null,
      });
    });
  };
  cancelReview = () => {
    if (!this.state.busy && !this.state.pending) this.set({ review: null });
  };
  apply = async () => {
    const captured = this.state.review;
    if (this.disposed || this.state.busy || this.state.pending || !captured)
      return;
    const pending = {
      action: captured.value.action,
      payload: clone(captured.payload),
      review: clone(captured.value),
      commandId: crypto.randomUUID(),
    };
    this.set({ pending, busy: true, error: null, review: null, result: null });
    try {
      this.accept(
        await this.io.execute(
          pending.action,
          clone(pending.payload),
          clone(pending.review),
          pending.commandId,
        ),
      );
    } catch {
      this.set({
        error:
          'The outcome is unconfirmed. Check the original receipt before another change.',
      });
    } finally {
      this.set({ busy: false });
    }
  };
  private accept(result: WikiResult) {
    const pending = this.state.pending;
    if (
      !pending ||
      result.command_id !== pending.commandId ||
      result.action !== pending.action
    )
      throw new Error('Wrong receipt');
    this.set({
      result: clone(result),
      pending: result.status === 'completed' ? null : pending,
      error:
        result.status === 'partial'
          ? 'Some effects may have completed. The original command will not be sent again.'
          : null,
    });
  }
  checkReceipt = async () => {
    const pending = this.state.pending;
    if (!pending) return;
    await this.read(async (signal) => {
      const value = await this.io.receipt(pending.commandId, signal);
      if (signal.aborted) return;
      if (value) this.accept(value);
      else
        this.set({
          error:
            'The original outcome is still unconfirmed. No action was repeated.',
        });
    });
  };
}

const labels: Record<WikiAction, string> = {
  'wiki.configure': 'Save wiki configuration',
  'wiki.publish': 'Publish this entity',
  'wiki.rebuild': 'Rebuild managed wiki files',
  'wiki.import': 'Accept reviewed vault version',
  'wiki.sync': 'Sync reviewed vault edits',
};
function Versions({ article }: { article: WikiArticleReview }) {
  return (
    <div className="stack">
      <h3>{article.title}</h3>
      {article.database_text !== null && (
        <Field label="Database version">
          <textarea
            className="input"
            aria-label={`Database version: ${article.title}`}
            readOnly
            rows={8}
            value={article.database_text}
          />
        </Field>
      )}
      <Field label="Vault version">
        <textarea
          className="input"
          aria-label={`Vault version: ${article.title}`}
          readOnly
          rows={8}
          value={article.vault_text}
        />
      </Field>
    </div>
  );
}
export default function WikiSettings({
  session,
}: {
  session: WikiSettingsSession;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => {
    if (!session.getSnapshot().status && !session.getSnapshot().busy)
      void session.load();
  }, [session]);
  const locked = state.busy || Boolean(state.pending);
  return (
    <section className="stack capability-page" aria-label="Wiki vault">
      <header className="capability-header">
        <div>
          <p className="eyebrow">Knowledge publishing</p>
          <h2>Wiki vault</h2>
          <p>
            Publish saved knowledge as Markdown. Opening articles and checking
            sync do not import, rebuild, or call a provider.
          </p>
        </div>
      </header>
      <div className="actions">
        <Button disabled={locked} onClick={() => void session.load()}>
          Reload wiki status
        </Button>
        {session.canChooseVault() && (
          <Button disabled={locked} onClick={() => void session.chooseVault()}>
            Choose authorized vault
          </Button>
        )}
      </div>
      {state.error && <p role="alert">{state.error}</p>}
      <p role="status">
        {state.busy
          ? 'Working…'
          : state.pending
            ? 'Original command awaiting confirmation'
            : state.result
              ? `${state.result.status === 'completed' ? 'Finished' : 'Partial outcome'}: ${state.result.count} completed, ${state.result.conflicts} need review.`
              : state.status?.availability === 'available' &&
                  state.status.articles === null
                ? 'Authorized folder selected. Review configuration to use it as the wiki vault.'
                : state.status?.availability === 'available'
                  ? `${state.status.articles} saved articles; ${state.status.edited} edits; ${state.status.conflicts} need review.`
                  : state.status?.availability === 'scope_required'
                    ? 'Select an authorized vault to read or change its files.'
                    : 'Wiki status is unavailable.'}
      </p>
      {state.pending && (
        <Button
          disabled={state.busy}
          onClick={() => void session.checkReceipt()}
        >
          Check original receipt
        </Button>
      )}
      {state.result?.status === 'completed' && (
        <p>Reload wiki status to review the current saved state.</p>
      )}
      {state.status && (
        <>
          <label>
            <input
              type="checkbox"
              checked={state.enabled}
              disabled={locked}
              onChange={(event) => session.enabled(event.target.checked)}
            />{' '}
            Enable wiki vault
          </label>
          <Button
            disabled={locked || state.status.availability !== 'available'}
            onClick={() => void session.review('wiki.configure')}
          >
            Review configuration
          </Button>
        </>
      )}
      {state.status?.availability === 'available' && state.status.enabled && (
        <>
          <Field label="Saved entity ID">
            <Input
              aria-label="Saved entity ID"
              value={state.entityId}
              maxLength={128}
              disabled={locked}
              onChange={(event) => session.entityId(event.target.value)}
            />
          </Field>
          <div className="actions">
            <Button
              disabled={locked || !state.entityId.trim()}
              onClick={() => void session.review('wiki.publish')}
            >
              Review publish
            </Button>
            <Button
              disabled={locked}
              onClick={() => void session.review('wiki.rebuild')}
            >
              Review rebuild
            </Button>
            <Button
              disabled={locked || state.selected.length === 0}
              onClick={() => void session.review('wiki.sync')}
            >
              Review {state.selected.length} selected edits
            </Button>
          </div>
          <p>
            Rebuild preserves user-authored and externally edited files.
            Conflicting versions require individual review.
          </p>
        </>
      )}
      {state.page && (
        <div className="stack" role="group" aria-label="Saved wiki articles">
          {state.page.items.length === 0 && <p>No saved wiki articles.</p>}
          {state.trimmed && (
            <p>
              Showing the latest 200 loaded articles. Reload returns to the
              beginning.
            </p>
          )}
          {state.page.items.map((article) => (
            <div className="panel-section stack" key={article.article_id}>
              <strong>{article.title}</strong>
              <span>{article.status.replaceAll('_', ' ')}</span>
              <div className="actions">
                {article.status === 'edited' && (
                  <label>
                    <input
                      type="checkbox"
                      aria-label={`Select edit: ${article.title}`}
                      disabled={
                        locked ||
                        (!state.selected.includes(article.article_id) &&
                          state.selected.length >= 50)
                      }
                      checked={state.selected.includes(article.article_id)}
                      onChange={(event) =>
                        session.select(article.article_id, event.target.checked)
                      }
                    />{' '}
                    Select edit
                  </label>
                )}
                <Button
                  disabled={state.busy || article.status === 'missing'}
                  onClick={() => void session.open(article.article_id)}
                >
                  Open {article.title}
                </Button>
                {['edited', 'conflict', 'legacy_review'].includes(
                  article.status,
                ) && (
                  <Button
                    disabled={locked}
                    onClick={() =>
                      void session.review('wiki.import', article.article_id)
                    }
                  >
                    Review versions: {article.title}
                  </Button>
                )}
              </div>
            </div>
          ))}
          {state.page.next_cursor && (
            <Button disabled={locked} onClick={() => void session.more()}>
              Load more articles
            </Button>
          )}
        </div>
      )}
      {state.article && (
        <div className="panel-section stack">
          <Versions article={state.article} />
          <Button disabled={state.busy} onClick={session.closeArticle}>
            Close article
          </Button>
        </div>
      )}
      {state.review && (
        <div
          className="panel-section stack"
          role="region"
          aria-label="Reviewed wiki action"
        >
          <h3>{labels[state.review.value.action]}</h3>
          <p>
            Only this reviewed vault and captured versions will be changed.
            Retained recovery copies and unrelated files remain available.
          </p>
          {state.review.value.action === 'wiki.configure' && (
            <p>
              Wiki will be {state.review.value.enabled ? 'enabled' : 'disabled'}
              . Rebuild is a separate action.
            </p>
          )}
          {state.review.value.articles.map((article) => (
            <Versions key={article.article_id} article={article} />
          ))}
          <div className="actions">
            <Button disabled={locked} onClick={session.cancelReview}>
              Cancel review
            </Button>
            <Button
              variant="primary"
              disabled={locked}
              onClick={() => void session.apply()}
            >
              {labels[state.review.value.action]}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
