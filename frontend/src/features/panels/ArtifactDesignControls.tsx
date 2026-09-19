import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  DesignFormSession,
  type DesignFormState,
} from './artifact-design-sessions';
import { Button, ErrorState, Field, Input, Select } from '../../ui/primitives';

export type DesignBrand = {
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  bg_color: string;
  text_color: string;
  heading_font: string;
  body_font: string;
  logo_asset_id: string;
  logo_mode: string;
  logo_scope: string;
  logo_position: string;
  logo_max_height: number;
  logo_padding: number;
};
export type DesignControlItem = {
  id: string;
  label: string;
  kind: string;
  detail: string;
  available: boolean;
};
export type DesignSection =
  'elements' | 'assets' | 'fonts' | 'presets' | 'interactions';
export type DesignControlsState = {
  resource_id: string;
  resource_revision: string;
  mode: string;
  page_id: string;
  brand: DesignBrand;
  element: {
    id: string;
    tag: string;
    styles: Record<string, string>;
    action: string;
  } | null;
  section: DesignSection;
  items: DesignControlItem[];
  item_count: number;
  next_cursor: string | null;
};
export type DesignReviewFinding = {
  id: string;
  source: string;
  category: string;
  severity: string;
  message: string;
  suggested_fix: string;
  page_id: string;
  auto_fixable: boolean;
};
export type DesignReviewState = {
  resource_id: string;
  resource_revision: string;
  page_id: string;
  scope: 'page' | 'project';
  heuristic: boolean;
  score: number;
  findings: DesignReviewFinding[];
  finding_count: number;
  next_cursor: string | null;
};
export type DesignControlsProps = {
  resourceId: string;
  session?: DesignFormSession;
  blocked?: boolean;
  resourceRevision: string;
  pageId: string;
  selectedElementId?: string;
  visible: boolean;
  load: (options: {
    page_id: string;
    element_id?: string;
    section: DesignSection;
    cursor?: string;
  }) => Promise<DesignControlsState>;
  review: (options: {
    page_id: string;
    scope: 'page' | 'project';
    cursor?: string;
  }) => Promise<DesignReviewState>;
  apply: (
    operation:
      | 'brand'
      | 'preset'
      | 'style'
      | 'hotspot'
      | 'review_fix'
      | 'asset_insert'
      | 'asset_remove'
      | 'asset_forget',
    payload: Record<string, unknown>,
    expectedRevision: string,
    pageId: string,
    elementId?: string,
  ) => Promise<{ resource_revision: string }>;
  upload: (
    file: File,
    expectedRevision: string,
  ) => Promise<{ resource_revision: string }>;
  onSelectElement: (elementId: string) => void;
  draftFix?: (
    findingId: string,
    pageId: string,
    expectedRevision: string,
  ) => Promise<string>;
  onDraftText?: (text: string) => void;
  mutatePreset?: (
    options: { action: 'save' | 'delete'; name: string; preset_id?: string },
    expectedRevision: string,
  ) => Promise<{
    resource_id: string;
    resource_revision: string;
    preset_id: string;
    name: string;
    action: 'save' | 'delete';
  }>;
};

const colors = [
  'primary_color',
  'secondary_color',
  'accent_color',
  'bg_color',
  'text_color',
] as const;
export default function ArtifactDesignControls(props: DesignControlsProps) {
  const {
    resourceId,
    resourceRevision,
    pageId,
    selectedElementId,
    visible,
    load,
  } = props;
  const [local] = useState(() => new DesignFormSession());
  const session = props.session ?? local;
  const retained = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const {
    section,
    state,
    brand,
    styles,
    review,
    scope,
    action,
    target,
    error,
    notice,
    loading,
    saving,
    file,
    presetName,
    presetReview,
    reload,
  } = retained;
  const sourceKey = JSON.stringify([
    resourceId,
    resourceRevision,
    pageId,
    selectedElementId ?? '',
  ]);
  const staleDraft = Boolean(
    retained.dirtySource && retained.dirtySource !== sourceKey,
  );
  const setter =
    <K extends keyof DesignFormState>(key: K, dirty = false) =>
    (
      value:
        | DesignFormState[K]
        | ((previous: DesignFormState[K]) => DesignFormState[K]),
    ) => {
      if (dirty) session.markDirty(key, sourceKey);
      session.set(key, value);
    };
  const setSection = setter('section');
  const setState = setter('state');
  const setBrand = setter('brand', true);
  const setStyles = setter('styles', true);
  const setReview = setter('review');
  const setScope = setter('scope');
  const setAction = setter('action', true);
  const setTarget = setter('target', true);
  const setError = setter('error');
  const setNotice = setter('notice');
  const setSaving = setter('saving');
  const setFile = setter('file', true);
  const setPresetName = setter('presetName', true);
  const setPresetReview = setter('presetReview');
  const setReload = setter('reload');
  useEffect(() => () => local.dispose(), [local]);
  const current = useRef(props);
  const operation = session.operation;
  useEffect(() => {
    current.current = props;
  }, [props]);
  useEffect(() => {
    let active = true;
    session.set('state', null);
    session.set('review', null);
    session.set('error', '');
    if (!visible) return;
    session.set('loading', true);
    void load({
      page_id: pageId,
      element_id: selectedElementId,
      section,
    })
      .then((value) => {
        if (!active) return;
        if (
          value.resource_id !== resourceId ||
          value.resource_revision !== resourceRevision ||
          value.page_id !== pageId
        )
          throw new Error('Resource changed');
        if (value.items.length > 50)
          throw new Error('Control page exceeded its bound');
        session.set('state', value);
        session.set('controlPage', false);
        if (!session.getSnapshot().dirtySource) {
          session.set('brand', value.brand);
          session.set('styles', value.element?.styles ?? {});
        }
      })
      .catch(() => {
        if (active)
          session.set(
            'error',
            'Current design controls could not be loaded. Reload to retry.',
          );
      })
      .finally(() => {
        if (active) session.set('loading', false);
      });
    return () => {
      active = false;
    };
  }, [
    resourceId,
    resourceRevision,
    pageId,
    selectedElementId,
    visible,
    load,
    section,
    reload,
    session,
  ]);
  useEffect(() => {
    session.set('notice', '');
  }, [session, props.resourceId, props.pageId, props.selectedElementId]);
  useEffect(() => {
    if (!session.getSnapshot().dirtySource) {
      session.set('file', null);
      session.set('presetReview', null);
      session.set('presetName', '');
    }
  }, [session, props.resourceId]);
  useEffect(() => {
    session.set('presetReview', null);
  }, [session, props.resourceRevision]);

  async function changePreset() {
    if (
      !presetReview ||
      !props.mutatePreset ||
      operation.current ||
      !state ||
      !props.visible
    )
      return;
    const token = Symbol('global-preset');
    operation.current = token;
    setSaving(true);
    setError('');
    const sourceId = props.resourceId;
    const sourceRevision = state.resource_revision;
    try {
      const result = await props.mutatePreset(presetReview, sourceRevision);
      if (
        current.current.resourceId !== sourceId ||
        current.current.resourceRevision !== sourceRevision
      )
        return;
      if (
        result.resource_id !== sourceId ||
        result.resource_revision !== sourceRevision
      )
        throw new Error('Resource changed');
      setPresetReview(null);
      session.committed('preset_' + presetReview.action);
      setNotice(
        result.action === 'delete'
          ? 'Global preset removed. Previous bytes are retained for recovery.'
          : 'Global preset saved.',
      );
      setReload((value) => value + 1);
    } catch {
      if (
        current.current.resourceId === sourceId &&
        current.current.resourceRevision === sourceRevision
      )
        setError(
          'Global preset change was not confirmed. Previous bytes are retained. Review recovery before retrying.',
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }

  async function upload() {
    if (!file || !state || operation.current || !props.visible) return;
    if (file.size === 0 || file.size > 25 * 1024 * 1024) {
      setError('Choose a nonempty asset up to 25 MiB.');
      return;
    }
    const token = Symbol('asset-upload');
    operation.current = token;
    setSaving(true);
    setError('');
    const sourceId = props.resourceId;
    try {
      const result = await props.upload(file, state.resource_revision);
      if (current.current.resourceId !== sourceId) return;
      session.set('file', null);
      session.committed('asset_upload');
      setNotice('Asset added.');
      if (result.resource_revision === current.current.resourceRevision)
        setReload((value) => value + 1);
      else setState(null);
    } catch {
      if (current.current.resourceId === sourceId)
        setError(
          'Asset attachment was not confirmed. Uploaded files are retained for recovery; reload before retrying.',
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }

  async function apply(
    kind: Parameters<DesignControlsProps['apply']>[0],
    payload: Record<string, unknown>,
    pageId = props.pageId,
  ) {
    if (operation.current || !state || !props.visible) return;
    const token = Symbol('design-control');
    operation.current = token;
    setSaving(true);
    setError('');
    setNotice('');
    const sourceId = props.resourceId;
    try {
      const result = await props.apply(
        kind,
        payload,
        state.resource_revision,
        pageId,
        props.selectedElementId,
      );
      if (
        current.current.resourceId === sourceId &&
        current.current.pageId === props.pageId &&
        current.current.selectedElementId === props.selectedElementId
      ) {
        session.committed(kind);
        setNotice('Changes saved.');
        setReview(null);
        if (result.resource_revision === current.current.resourceRevision)
          setReload((value) => value + 1);
        else setState(null);
      }
    } catch {
      if (
        current.current.resourceId === sourceId &&
        current.current.pageId === props.pageId &&
        current.current.selectedElementId === props.selectedElementId
      )
        setError(
          'Changes were not confirmed. Reload the current design before retrying.',
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }
  async function scan(cursor?: string) {
    if (operation.current || !state) return;
    const token = Symbol('design-review');
    operation.current = token;
    setSaving(true);
    setError('');
    const source = state;
    try {
      const value = await props.review({
        page_id: source.page_id,
        scope,
        cursor,
      });
      if (
        current.current.resourceId !== source.resource_id ||
        current.current.resourceRevision !== source.resource_revision ||
        current.current.pageId !== source.page_id
      )
        return;
      if (
        value.resource_id !== source.resource_id ||
        value.resource_revision !== source.resource_revision
      )
        throw new Error('Resource changed');
      if (value.findings.length > 50)
        throw new Error('Review page exceeded its bound');
      setReview(value);
      session.set('reviewPage', Boolean(cursor));
    } catch {
      if (
        current.current.resourceId === source.resource_id &&
        current.current.resourceRevision === source.resource_revision &&
        current.current.pageId === source.page_id
      ) {
        setReview(null);
        setError(
          'Review is unavailable. No clean result has been established.',
        );
      }
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }
  async function draft(finding: DesignReviewFinding) {
    if (!props.draftFix || !props.onDraftText || !state || operation.current)
      return;
    const source = state;
    const token = Symbol('review-draft');
    operation.current = token;
    setSaving(true);
    setError('');
    try {
      const text = await props.draftFix(
        finding.id,
        finding.page_id,
        source.resource_revision,
      );
      if (
        current.current.resourceId === source.resource_id &&
        current.current.resourceRevision === source.resource_revision &&
        current.current.pageId === source.page_id
      ) {
        props.onDraftText(text);
        setNotice('AI fix drafted in chat. Review and send it when ready.');
      }
    } catch {
      if (
        current.current.resourceId === source.resource_id &&
        current.current.resourceRevision === source.resource_revision
      )
        setError(
          'The fix draft is unavailable. Run the current design review again.',
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }
  async function more() {
    if (!state?.next_cursor || operation.current) return;
    const token = Symbol('design-page');
    operation.current = token;
    setSaving(true);
    const source = state;
    try {
      const value = await props.load({
        page_id: props.pageId,
        element_id: props.selectedElementId,
        section,
        cursor: state.next_cursor,
      });
      if (
        current.current.resourceId !== source.resource_id ||
        current.current.resourceRevision !== source.resource_revision ||
        current.current.pageId !== source.page_id
      )
        return;
      if (
        value.resource_id !== source.resource_id ||
        value.resource_revision !== source.resource_revision ||
        value.section !== source.section
      )
        throw new Error('Resource changed');
      if (value.items.length > 50)
        throw new Error('Control page exceeded its bound');
      setState(value);
      session.set('controlPage', true);
    } catch {
      if (
        current.current.resourceId === source.resource_id &&
        current.current.pageId === source.page_id
      )
        setError(
          'More controls could not be loaded. Reload the current design.',
        );
    } finally {
      if (operation.current === token) {
        operation.current = null;
        setSaving(false);
      }
    }
  }
  if (!props.visible) return null;
  const busy = loading || saving || staleDraft || Boolean(props.blocked);
  return (
    <section
      className="studio-section stack"
      aria-label="Design controls"
      aria-busy={busy}
    >
      {staleDraft && (
        <p role="status">
          Your unsaved design draft belongs to an earlier page, element or
          revision. Return to that selection or explicitly discard the draft
          before editing the current design.
        </p>
      )}
      {retained.dirtySource && (
        <Button
          disabled={saving || Boolean(props.blocked)}
          onClick={() => session.reset()}
        >
          Discard design draft
        </Button>
      )}
      {error && (
        <ErrorState title="Design controls unavailable">{error}</ErrorState>
      )}
      {notice && <p role="status">{notice}</p>}
      <Button disabled={busy} onClick={() => setReload((value) => value + 1)}>
        Reload controls
      </Button>
      {brand && state && (
        <details className="capability-section stack">
          <summary>Brand and fonts</summary>
          {colors.map((key) => (
            <Field label={key.replaceAll('_', ' ')} key={key}>
              <Input
                aria-label={key.replaceAll('_', ' ')}
                value={brand[key]}
                disabled={busy}
                maxLength={7}
                onChange={(event) =>
                  setBrand({ ...brand, [key]: event.target.value })
                }
              />
            </Field>
          ))}
          {(['heading_font', 'body_font'] as const).map((key) => (
            <Field label={key.replaceAll('_', ' ')} key={key}>
              <Input
                aria-label={key.replaceAll('_', ' ')}
                value={brand[key]}
                disabled={busy}
                maxLength={128}
                onChange={(event) =>
                  setBrand({ ...brand, [key]: event.target.value })
                }
              />
            </Field>
          ))}
          <p>
            Use a bundled, cached or system font from the font list. Opening
            this panel does not download fonts.
          </p>
          <Field label="Logo asset">
            <Input
              aria-label="Logo asset"
              disabled={busy}
              value={brand.logo_asset_id}
              maxLength={256}
              onChange={(event) =>
                setBrand({ ...brand, logo_asset_id: event.target.value })
              }
            />
          </Field>
          <Field label="Logo placement">
            <Select
              aria-label="Logo placement"
              disabled={busy}
              value={brand.logo_position}
              onChange={(event) =>
                setBrand({ ...brand, logo_position: event.target.value })
              }
            >
              {['top_left', 'top_right', 'bottom_left', 'bottom_right'].map(
                (key) => (
                  <option key={key} value={key}>
                    {key.replaceAll('_', ' ')}
                  </option>
                ),
              )}
            </Select>
          </Field>
          <Field label="Logo mode">
            <Select
              aria-label="Logo mode"
              disabled={busy}
              value={brand.logo_mode}
              onChange={(event) =>
                setBrand({ ...brand, logo_mode: event.target.value })
              }
            >
              <option value="auto">Automatic overlay</option>
              <option value="manual">Manual placeholder</option>
            </Select>
          </Field>
          <Field label="Logo scope">
            <Select
              aria-label="Logo scope"
              disabled={busy}
              value={brand.logo_scope}
              onChange={(event) =>
                setBrand({ ...brand, logo_scope: event.target.value })
              }
            >
              <option value="all">All pages</option>
              <option value="first">First page</option>
            </Select>
          </Field>
          {(['logo_max_height', 'logo_padding'] as const).map((key) => (
            <Field key={key} label={key.replaceAll('_', ' ')}>
              <Input
                type="number"
                aria-label={key.replaceAll('_', ' ')}
                disabled={busy}
                value={brand[key]}
                onChange={(event) =>
                  setBrand({ ...brand, [key]: Number(event.target.value) })
                }
              />
            </Field>
          ))}
          <Button disabled={busy} onClick={() => void apply('brand', brand)}>
            Apply brand
          </Button>
        </details>
      )}
      {state?.element && (
        <details className="capability-section stack" open>
          <summary>Selected element properties</summary>
          <p>{state.element.tag}</p>
          {[
            'color',
            'background-color',
            'font-size',
            'font-weight',
            'line-height',
            'padding',
            'margin',
            'gap',
            'border-radius',
            'width',
            'height',
          ].map((key) => (
            <Field key={key} label={key}>
              <Input
                aria-label={`Element ${key}`}
                maxLength={256}
                disabled={busy}
                value={styles[key] ?? ''}
                onChange={(event) =>
                  setStyles({ ...styles, [key]: event.target.value })
                }
              />
            </Field>
          ))}
          <Button disabled={busy} onClick={() => void apply('style', styles)}>
            Apply properties
          </Button>
          {['landing', 'app_mockup', 'storyboard'].includes(state.mode) && (
            <>
              <Field label="Hotspot action">
                <Select
                  aria-label="Hotspot action"
                  disabled={busy}
                  value={action}
                  onChange={(event) => setAction(event.target.value)}
                >
                  <option value="navigate">Navigate to screen</option>
                  <option value="toggle_state">Toggle state</option>
                  <option value="play_media">Play media</option>
                  <option value="clear">Clear interaction</option>
                </Select>
              </Field>
              {action !== 'clear' && (
                <Field label="Hotspot target">
                  <Input
                    aria-label="Hotspot target"
                    disabled={busy}
                    value={target}
                    maxLength={128}
                    onChange={(event) => setTarget(event.target.value)}
                  />
                </Field>
              )}
              <Button
                disabled={busy}
                onClick={() => void apply('hotspot', { action, target })}
              >
                Apply hotspot
              </Button>
            </>
          )}
        </details>
      )}
      <Field label="Design catalog">
        <Select
          aria-label="Design catalog"
          disabled={busy}
          value={section}
          onChange={(event) => setSection(event.target.value as DesignSection)}
        >
          {(
            ['elements', 'assets', 'fonts', 'presets', 'interactions'] as const
          ).map((key) => (
            <option key={key} value={key}>
              {key}
            </option>
          ))}
        </Select>
      </Field>
      {section === 'presets' && props.mutatePreset && (
        <>
          <Field label="New global preset name">
            <Input
              aria-label="New global preset name"
              value={presetName}
              maxLength={256}
              disabled={busy}
              onChange={(event) => setPresetName(event.target.value)}
            />
          </Field>
          <Button
            disabled={busy || !state || !presetName.trim()}
            onClick={() =>
              setPresetReview({ action: 'save', name: presetName.trim() })
            }
          >
            Save current brand as preset
          </Button>
          {presetReview && (
            <div role="group" aria-label="Global preset review">
              <p>
                {presetReview.action === 'delete'
                  ? 'Delete'
                  : presetReview.preset_id
                    ? 'Replace'
                    : 'Save'}{' '}
                global preset “{presetReview.name}”. This changes the shared
                catalog for all projects; saved project brands stay unchanged.
              </p>
              <Button disabled={busy} onClick={() => void changePreset()}>
                Confirm global preset change
              </Button>
              <Button disabled={busy} onClick={() => setPresetReview(null)}>
                Cancel preset change
              </Button>
            </div>
          )}
        </>
      )}
      {section === 'assets' && (
        <>
          {file && <p>Selected local asset: {file.name}</p>}
          <Field label="Choose asset">
            <Input
              type="file"
              aria-label="Choose asset"
              disabled={busy}
              accept=".png,.jpg,.jpeg,.webp,.gif,.svg,.mp4,.m4v,.webm,.wav,.ogg,.mp3"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </Field>
          <Button
            disabled={busy || !file || !state}
            onClick={() => void upload()}
          >
            Add asset
          </Button>
          <p>
            Assets remain saved when removed from a page. Original files are
            retained for history and recovery.
          </p>
        </>
      )}
      {state && (
        <>
          <p>
            {state.items.length} of {state.item_count} {section}
          </p>
          <ul>
            {state.items.map((item) => (
              <li key={item.id}>
                <span>
                  {item.label} · {item.detail}
                </span>
                {section === 'elements' && (
                  <Button
                    disabled={busy || !item.available}
                    onClick={() => props.onSelectElement(item.id)}
                  >
                    Select {item.label}
                  </Button>
                )}
                {section === 'presets' && (
                  <>
                    <Button
                      disabled={busy || !item.available}
                      onClick={() =>
                        void apply('preset', { preset_id: item.id })
                      }
                    >
                      Apply {item.label}
                    </Button>
                    {props.mutatePreset && (
                      <>
                        <Button
                          disabled={busy || !item.available}
                          onClick={() =>
                            setPresetReview({
                              action: 'save',
                              name: item.label,
                              preset_id: item.id,
                            })
                          }
                        >
                          Replace {item.label} with current brand
                        </Button>
                        <Button
                          disabled={busy || !item.available}
                          onClick={() =>
                            setPresetReview({
                              action: 'delete',
                              name: item.label,
                              preset_id: item.id,
                            })
                          }
                        >
                          Delete {item.label} preset
                        </Button>
                      </>
                    )}
                  </>
                )}
                {section === 'assets' && (
                  <>
                    <Button
                      disabled={busy || !item.available}
                      onClick={() =>
                        void apply('asset_insert', { asset_id: item.id })
                      }
                    >
                      Insert {item.label}
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void apply('asset_remove', { asset_id: item.id })
                      }
                    >
                      Remove {item.label} from page
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void apply('asset_forget', { asset_id: item.id })
                      }
                    >
                      Remove {item.label} from list
                    </Button>
                  </>
                )}
              </li>
            ))}
          </ul>
          {retained.controlPage && (
            <Button
              disabled={busy}
              onClick={() => setReload((value) => value + 1)}
            >
              First controls page
            </Button>
          )}
          {state.next_cursor && (
            <Button disabled={busy} onClick={() => void more()}>
              Next controls page
            </Button>
          )}
        </>
      )}
      <Field label="Review scope">
        <Select
          aria-label="Review scope"
          disabled={busy}
          value={scope}
          onChange={(event) => {
            setScope(event.target.value as 'page' | 'project');
            setReview(null);
          }}
        >
          <option value="page">Current page</option>
          <option value="project">Whole project</option>
        </Select>
      </Field>
      <Button disabled={busy || !state} onClick={() => void scan()}>
        Run design review
      </Button>
      <p>
        Heuristic critique and brand lint report representative findings. They
        do not replace visual or accessibility verification. Safe fixes apply
        the selected category across its page.
      </p>
      {review && (
        <div role="group" aria-label="Design review">
          <p>
            Heuristic score {review.score} · {review.findings.length} of{' '}
            {review.finding_count} findings
          </p>
          <ul>
            {review.findings.map((finding) => (
              <li key={finding.id}>
                <p>
                  {finding.severity}: {finding.message}
                </p>
                <p>{finding.suggested_fix}</p>
                {props.draftFix && props.onDraftText && (
                  <Button disabled={busy} onClick={() => void draft(finding)}>
                    Draft AI fix in chat
                  </Button>
                )}
                {finding.auto_fixable && (
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void apply(
                        'review_fix',
                        { finding_id: finding.id },
                        finding.page_id,
                      )
                    }
                  >
                    Apply safe {finding.category} fix
                  </Button>
                )}
              </li>
            ))}
          </ul>
          {retained.reviewPage && (
            <Button disabled={busy} onClick={() => void scan()}>
              First findings page
            </Button>
          )}
          {review.next_cursor && (
            <Button
              disabled={busy}
              onClick={() => void scan(review.next_cursor ?? undefined)}
            >
              Next findings page
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
