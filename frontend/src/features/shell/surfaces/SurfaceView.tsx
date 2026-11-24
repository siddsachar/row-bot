import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Bell,
  BellOff,
  Calendar,
  CalendarDays,
  Camera,
  Check,
  CircleAlert,
  CircleHelp,
  CircleUserRound,
  Download,
  Eye,
  EyeOff,
  FastForward,
  Folder,
  Heart,
  HeartOff,
  House,
  ImageIcon,
  Info,
  Lock,
  LockOpen,
  Mail,
  MapPin,
  Menu,
  Minus,
  MoreHorizontal,
  MoreVertical,
  Paperclip,
  Pause,
  Pencil,
  Phone,
  Play,
  Plus,
  Printer,
  RefreshCw,
  Rewind,
  Search,
  Send,
  Settings,
  Share2,
  ShoppingCart,
  SkipBack,
  SkipForward,
  Square,
  Star,
  StarHalf,
  StarOff,
  Trash2,
  TriangleAlert,
  Upload,
  User,
  Volume,
  Volume1,
  Volume2,
  VolumeX,
  X,
  ExternalLink,
  Maximize2,
  Pin,
  PinOff,
  type LucideIcon,
} from 'lucide-react';
import { useOptionalRuntime } from '../../../runtime';
import { ModalTask, useNotify } from '../../../ui/overlays';
import { Button, Hint, IconButton, Input, Tabs } from '../../../ui/primitives';
import {
  sendPrompt,
  sendPromptWhenOpen,
  usePromptReady,
  type SurfacePress,
} from '../composer-bridge';
import { clientError } from '../../../api/errors';
import { pinned, pinSurface, unpinSurface, useBoard } from './board-store';
import {
  changeAnswerState,
  useAnswerState,
} from '../answer-components/answer-state';
import {
  asText,
  boundPath,
  drawableMedia,
  evaluate,
  failedCheck,
  openableUrl,
  readPath,
  readSurface,
  writePath,
  type DataModel,
  type Scope,
  type SurfaceComponent,
  type SurfaceSpec,
} from './a2ui';
import { SurfaceText } from './SurfaceText';
import { requestSurfacePanel } from './open-surface';
import {
  sourceWords,
  useSurfaceSources,
  withoutSources,
  withSources,
  type SourceState,
} from './sources';
import {
  setSurfaceModel,
  useLatestSurfaceBlock,
  useSurfaceModel,
} from './surface-store';
import { accessible, text, weighted, type Context } from './context';
import { RowBotComponent, ROWBOT_COMPONENTS } from './rowbot-components';

const ICONS: Record<string, LucideIcon> = {
  accountCircle: CircleUserRound,
  add: Plus,
  arrowBack: ArrowLeft,
  arrowForward: ArrowRight,
  attachFile: Paperclip,
  calendarToday: CalendarDays,
  call: Phone,
  camera: Camera,
  check: Check,
  close: X,
  delete: Trash2,
  download: Download,
  edit: Pencil,
  event: Calendar,
  error: CircleAlert,
  fastForward: FastForward,
  favorite: Heart,
  favoriteOff: HeartOff,
  folder: Folder,
  help: CircleHelp,
  home: House,
  info: Info,
  locationOn: MapPin,
  lock: Lock,
  lockOpen: LockOpen,
  mail: Mail,
  menu: Menu,
  moreVert: MoreVertical,
  moreHoriz: MoreHorizontal,
  notificationsOff: BellOff,
  notifications: Bell,
  pause: Pause,
  payment: ShoppingCart,
  person: User,
  phone: Phone,
  photo: ImageIcon,
  play: Play,
  print: Printer,
  refresh: RefreshCw,
  rewind: Rewind,
  search: Search,
  send: Send,
  settings: Settings,
  share: Share2,
  shoppingCart: ShoppingCart,
  skipNext: SkipForward,
  skipPrevious: SkipBack,
  star: Star,
  starHalf: StarHalf,
  starOff: StarOff,
  stop: Square,
  upload: Upload,
  visibility: Eye,
  visibilityOff: EyeOff,
  volumeDown: Volume1,
  volumeMute: Volume,
  volumeOff: VolumeX,
  volumeUp: Volume2,
  warning: TriangleAlert,
};

const MAX_DEPTH = 24;
const SAVE_DELAY = 800;
/** How often a pinned surface reads its sources again while Home is shown. */
const BOARD_REFRESH = 10 * 60 * 1000;

/** Where a surface's button press goes: this chat, as a press on this reply. */
export type SurfaceTarget = { conversationId?: string; messageId?: string };

const FLEX: Record<string, string> = {
  start: 'flex-start',
  center: 'center',
  end: 'flex-end',
  stretch: 'stretch',
  spaceBetween: 'space-between',
  spaceAround: 'space-around',
  spaceEvenly: 'space-evenly',
};

/** The ids (or template items) a container draws, with each child's scope. */
function childList(
  value: unknown,
  context: Context,
  scope: Scope,
): { id: string; scope: Scope; key: string }[] {
  if (Array.isArray(value))
    return value
      .filter((id): id is string => typeof id === 'string')
      .map((id) => ({ id, scope, key: id }));
  if (value && typeof value === 'object') {
    const template = value as { path?: string; componentId?: string };
    if (!template.path || !template.componentId) return [];
    const items = readPath(context.model, template.path, scope);
    if (!Array.isArray(items)) return [];
    const base =
      template.path.startsWith('/') || !scope
        ? template.path.startsWith('/')
          ? template.path
          : `/${template.path}`
        : `${scope.base}/${template.path}`;
    return items.slice(0, 200).map((_item, index) => ({
      id: template.componentId!,
      scope: { base: `${base}/${index}`, index },
      key: `${template.componentId}:${index}`,
    }));
  }
  return [];
}

function Node({
  id,
  scope,
  context,
  depth,
}: {
  id: string;
  scope: Scope;
  context: Context;
  depth: number;
}): ReactNode {
  const component = context.byId.get(id);
  if (!component || depth > MAX_DEPTH) return null;
  const child = (childId: unknown, childScope: Scope = scope) =>
    typeof childId === 'string' ? (
      <Node
        id={childId}
        scope={childScope}
        context={context}
        depth={depth + 1}
      />
    ) : null;
  const children = (value: unknown) =>
    childList(value, context, scope).map((item) => (
      <Fragment key={item.key}>
        <Node
          id={item.id}
          scope={item.scope}
          context={context}
          depth={depth + 1}
        />
      </Fragment>
    ));
  const props = {
    ...accessible(component, context, scope),
    style: weighted(component),
  };
  switch (component.component) {
    case 'Text':
      return (
        <SurfaceText
          text={text(component.text, context, scope)}
          caption={component.variant === 'caption'}
          {...props}
        />
      );
    case 'Image':
      return (
        <Media
          component={component}
          context={context}
          scope={scope}
          kind="image"
        />
      );
    case 'Video':
      return (
        <Media
          component={component}
          context={context}
          scope={scope}
          kind="video"
        />
      );
    case 'AudioPlayer':
      return (
        <Media
          component={component}
          context={context}
          scope={scope}
          kind="audio"
        />
      );
    case 'Icon': {
      const name =
        typeof component.name === 'string'
          ? component.name
          : text(component.name, context, scope);
      const Glyph = ICONS[name];
      return Glyph ? (
        <Glyph className="genui-icon" size={18} aria-hidden {...props} />
      ) : null;
    }
    case 'Row':
    case 'Column':
      return (
        <div
          className={`genui-${component.component.toLowerCase()}`}
          {...props}
          style={{
            ...props.style,
            justifyContent: FLEX[String(component.justify ?? 'start')],
            alignItems:
              FLEX[
                String(
                  component.align ??
                    (component.component === 'Row' ? 'center' : 'stretch'),
                )
              ],
          }}
        >
          {children(component.children)}
        </div>
      );
    case 'List':
      return (
        <div
          className="genui-list"
          role="list"
          data-direction={
            component.direction === 'horizontal' ? 'horizontal' : 'vertical'
          }
          {...props}
        >
          {childList(component.children, context, scope).map((item) => (
            <div role="listitem" key={item.key} className="genui-list-item">
              <Node
                id={item.id}
                scope={item.scope}
                context={context}
                depth={depth + 1}
              />
            </div>
          ))}
        </div>
      );
    case 'Card':
      return (
        <div className="genui-card" {...props}>
          {child(component.child)}
        </div>
      );
    case 'Divider':
      return (
        <hr
          className="genui-divider"
          data-axis={component.axis === 'vertical' ? 'vertical' : undefined}
          {...props}
        />
      );
    case 'Tabs':
      return (
        <SurfaceTabs
          component={component}
          context={context}
          scope={scope}
          depth={depth}
        />
      );
    case 'Modal':
      return (
        <SurfaceModal
          component={component}
          context={context}
          scope={scope}
          depth={depth}
        />
      );
    case 'Button':
      return (
        <SurfaceButton
          component={component}
          context={context}
          scope={scope}
          depth={depth}
        />
      );
    case 'TextField':
    case 'CheckBox':
    case 'ChoicePicker':
    case 'Slider':
    case 'DateTimeInput':
      return (
        <SurfaceInput component={component} context={context} scope={scope} />
      );
    default:
      // Row-Bot's own components (charts, tables, metrics...), drawn from the same context.
      return ROWBOT_COMPONENTS.has(component.component) ? (
        <RowBotComponent
          component={component}
          context={context}
          scope={scope}
        />
      ) : null;
  }
}

function Media({
  component,
  context,
  scope,
  kind,
}: {
  component: SurfaceComponent;
  context: Context;
  scope: Scope;
  kind: 'image' | 'video' | 'audio';
}) {
  const url = text(component.url, context, scope);
  const description = text(component.description, context, scope);
  if (drawableMedia(url, kind)) {
    if (kind === 'image')
      return (
        <img
          className="genui-image"
          src={url}
          alt={description}
          data-variant={String(component.variant ?? 'mediumFeature')}
          style={{
            objectFit: String(
              component.fit ?? 'cover',
            ) as CSSProperties['objectFit'],
          }}
        />
      );
    if (kind === 'video')
      return (
        <video className="genui-video" src={url} controls preload="metadata" />
      );
    return (
      <audio
        className="genui-audio"
        src={url}
        controls
        preload="metadata"
        aria-label={description}
      />
    );
  }
  // Nothing is fetched by itself: a link elsewhere is the person's to open, and only one written into the
  // interface, never one made from its data (which may hold what Row-Bot filled in on this computer).
  const link = typeof component.url === 'string' ? openableUrl(url) : null;
  const label =
    kind === 'image' ? 'Picture' : kind === 'video' ? 'Video' : 'Audio';
  return (
    <div className="genui-media-link" data-kind={kind}>
      <ImageIcon size={16} aria-hidden />
      <span>
        {description || label}
        {link && <small>{link.host}</small>}
      </span>
      {link && (
        <Button
          variant="ghost"
          onClick={() =>
            window.open(link.href, '_blank', 'noopener,noreferrer')
          }
        >
          <ExternalLink size={14} aria-hidden />
          Open
        </Button>
      )}
    </div>
  );
}

function SurfaceTabs({
  component,
  context,
  scope,
  depth,
}: {
  component: SurfaceComponent;
  context: Context;
  scope: Scope;
  depth: number;
}) {
  const tabs = (component.tabs as { title: unknown; child: string }[]) ?? [];
  const [value, setValue] = useState('0');
  return (
    <Tabs
      className="genui-tabs"
      label={text(tabs[0]?.title, context, scope) || 'Sections'}
      value={value}
      onChange={setValue}
      items={tabs.map((tab, index) => ({
        id: String(index),
        label: text(tab.title, context, scope),
        content: (
          <Node
            id={tab.child}
            scope={scope}
            context={context}
            depth={depth + 1}
          />
        ),
      }))}
    />
  );
}

function SurfaceModal({
  component,
  context,
  scope,
  depth,
}: {
  component: SurfaceComponent;
  context: Context;
  scope: Scope;
  depth: number;
}) {
  const [open, setOpen] = useState(false);
  const content = context.byId.get(String(component.content));
  const title =
    content?.component === 'Text' ? text(content.text, context, scope) : '';
  return (
    <>
      <div
        className="genui-modal-trigger"
        onClickCapture={(event) => {
          // The trigger opens the dialog; its own action waits for the dialog.
          event.preventDefault();
          event.stopPropagation();
          setOpen(true);
        }}
      >
        <Node
          id={String(component.trigger)}
          scope={scope}
          context={context}
          depth={depth + 1}
        />
      </div>
      <ModalTask
        open={open}
        onOpenChange={setOpen}
        title={title.slice(0, 80) || 'Details'}
        description={context.title}
        ariaLabel={title.slice(0, 80) || 'Details'}
        className="genui-modal"
      >
        <div className="genui-modal-body">
          <Node
            id={String(component.content)}
            scope={scope}
            context={context}
            depth={depth + 1}
          />
        </div>
      </ModalTask>
    </>
  );
}

function SurfaceButton({
  component,
  context,
  scope,
  depth,
}: {
  component: SurfaceComponent;
  context: Context;
  scope: Scope;
  depth: number;
}) {
  const action = component.action as
    | {
        event?: { name: string; context?: Record<string, unknown> };
        functionCall?: unknown;
      }
    | undefined;
  const failed = failedCheck(component, context.model, scope);
  const child = context.byId.get(String(component.child));
  const label =
    child?.component === 'Text' ? text(child.text, context, scope) : '';
  const event = action?.event;
  const isPressed = !!event && context.pressed === event.name;
  const variant =
    component.variant === 'primary'
      ? 'primary'
      : component.variant === 'borderless'
        ? 'ghost'
        : 'secondary';
  const click = () => {
    if (event) {
      const values: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(event.context ?? {}))
        values[key] = evaluate(value, context.model, scope);
      context.press({
        name: event.name,
        componentId: component.id,
        context: values,
        label: label || event.name,
      });
      return;
    }
    const call = action?.functionCall as
      { '@call'?: string; args?: { url?: unknown } } | undefined;
    if (call?.['@call'] === 'openUrl') {
      const link = openableUrl(
        asText(evaluate(call.args?.url, context.model, scope)),
      );
      if (link) window.open(link.href, '_blank', 'noopener,noreferrer');
    }
  };
  return (
    <span className="genui-button" style={weighted(component)}>
      <Button
        variant={variant}
        disabled={!!failed || (!!event && !context.ready)}
        aria-busy={isPressed || undefined}
        data-pressed={isPressed ? 'true' : undefined}
        title={failed ?? undefined}
        {...accessible(component, context, scope)}
        onClick={click}
      >
        <Node
          id={String(component.child)}
          scope={scope}
          context={context}
          depth={depth + 1}
        />
      </Button>
      {failed && <small className="genui-check-message">{failed}</small>}
    </span>
  );
}

function SurfaceInput({
  component,
  context,
  scope,
}: {
  component: SurfaceComponent;
  context: Context;
  scope: Scope;
}) {
  const id = `${context.uid}-${component.id}-${scope?.index ?? ''}`;
  const label = text(component.label, context, scope);
  const path = boundPath(component.value);
  const value = evaluate(component.value, context.model, scope);
  const set = (next: unknown) => {
    if (path) context.set(path, next, scope);
  };
  const failed = failedCheck(component, context.model, scope);
  const a11y = accessible(component, context, scope);
  const message = failed && (
    <small className="genui-check-message" id={`${id}-check`}>
      {failed}
    </small>
  );
  const described = failed ? `${id}-check` : undefined;
  switch (component.component) {
    case 'CheckBox':
      return (
        <label className="genui-checkbox" style={weighted(component)}>
          <input
            type="checkbox"
            checked={value === true}
            disabled={!path}
            onChange={(event) => set(event.currentTarget.checked)}
            aria-describedby={described}
            {...a11y}
          />
          <span>{label}</span>
          {message}
        </label>
      );
    case 'Slider': {
      const min = typeof component.min === 'number' ? component.min : 0;
      const max = typeof component.max === 'number' ? component.max : 100;
      const steps =
        typeof component.steps === 'number' && component.steps > 0
          ? component.steps
          : 0;
      const current = typeof value === 'number' ? value : Number(value) || min;
      return (
        <div className="genui-field genui-slider" style={weighted(component)}>
          <div className="genui-slider-head">
            <label htmlFor={id}>{label}</label>
            <output htmlFor={id}>{asText(current)}</output>
          </div>
          <input
            id={id}
            type="range"
            min={min}
            max={max}
            step={steps ? (max - min) / steps : 'any'}
            value={current}
            disabled={!path}
            onChange={(event) => set(Number(event.currentTarget.value))}
            aria-describedby={described}
            {...a11y}
          />
          {message}
        </div>
      );
    }
    case 'ChoicePicker':
      return (
        <ChoicePicker
          component={component}
          context={context}
          scope={scope}
          id={id}
          label={label}
          value={value}
          set={path ? set : undefined}
          message={message}
        />
      );
    case 'DateTimeInput': {
      const date = component.enableDate !== false;
      const time = component.enableTime === true;
      const type = date && time ? 'datetime-local' : time ? 'time' : 'date';
      return (
        <div className="genui-field" style={weighted(component)}>
          {label && <label htmlFor={id}>{label}</label>}
          <Input
            id={id}
            type={type}
            value={asText(value)}
            min={
              component.min ? text(component.min, context, scope) : undefined
            }
            max={
              component.max ? text(component.max, context, scope) : undefined
            }
            disabled={!path}
            onChange={(event) => set(event.currentTarget.value)}
            aria-describedby={described}
            aria-invalid={!!failed || undefined}
            {...a11y}
          />
          {message}
        </div>
      );
    }
    default: {
      const variant = String(component.variant ?? 'shortText');
      const placeholder = component.placeholder
        ? text(component.placeholder, context, scope)
        : undefined;
      const shared = {
        id,
        value: asText(value),
        placeholder,
        disabled: !path,
        'aria-describedby': described,
        'aria-invalid': !!failed || undefined,
        ...a11y,
      };
      return (
        <div className="genui-field" style={weighted(component)}>
          <label htmlFor={id}>{label}</label>
          {variant === 'longText' ? (
            <textarea
              className="input genui-textarea"
              rows={3}
              {...shared}
              onChange={(event) => set(event.currentTarget.value)}
            />
          ) : (
            <Input
              type={
                variant === 'number'
                  ? 'number'
                  : variant === 'obscured'
                    ? 'password'
                    : 'text'
              }
              inputMode={variant === 'number' ? 'decimal' : undefined}
              {...shared}
              onChange={(event) => {
                const raw = event.currentTarget.value;
                // A number field writes a number, so totals add rather than join.
                set(
                  variant === 'number' &&
                    raw !== '' &&
                    Number.isFinite(Number(raw))
                    ? Number(raw)
                    : raw,
                );
              }}
            />
          )}
          {message}
        </div>
      );
    }
  }
}

function ChoicePicker({
  component,
  context,
  scope,
  id,
  label,
  value,
  set,
  message,
}: {
  component: SurfaceComponent;
  context: Context;
  scope: Scope;
  id: string;
  label: string;
  value: unknown;
  set?: (value: unknown) => void;
  message: ReactNode;
}) {
  const options =
    (component.options as { label: unknown; value: string }[]) ?? [];
  const multiple = component.variant === 'multipleSelection';
  const chosen = Array.isArray(value) ? value.map(String) : [];
  const [filter, setFilter] = useState('');
  const shown = options.filter((option) =>
    text(option.label, context, scope)
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  const toggle = (option: string) => {
    if (!set) return;
    if (multiple)
      set(
        chosen.includes(option)
          ? chosen.filter((item) => item !== option)
          : [...chosen, option],
      );
    else set([option]);
  };
  const chips = component.displayStyle === 'chips';
  return (
    <fieldset
      className="genui-field genui-choices"
      data-style={chips ? 'chips' : 'list'}
      style={weighted(component)}
    >
      {label && <legend>{label}</legend>}
      {component.filterable === true && (
        <Input
          type="search"
          placeholder="Filter"
          aria-label={`Filter ${label || 'options'}`}
          value={filter}
          onChange={(event) => setFilter(event.currentTarget.value)}
        />
      )}
      <div className="genui-choice-options">
        {shown.map((option) => {
          const selected = chosen.includes(option.value);
          const optionLabel = text(option.label, context, scope);
          return chips ? (
            <button
              type="button"
              key={option.value}
              className="genui-chip"
              aria-pressed={selected}
              disabled={!set}
              onClick={() => toggle(option.value)}
            >
              {selected && <Check size={14} aria-hidden />}
              {optionLabel}
            </button>
          ) : (
            <label key={option.value} className="genui-choice">
              <input
                type={multiple ? 'checkbox' : 'radio'}
                name={id}
                checked={selected}
                disabled={!set}
                onChange={() => toggle(option.value)}
              />
              <span>{optionLabel}</span>
            </label>
          );
        })}
      </div>
      {message}
    </fieldset>
  );
}

/**
 * One surface a reply drew (A2UI 1.0, generative UI): Row-Bot's own
 * components, laid out as the reply wrote them. What the person enters stays
 * here (and beside the chat, so it follows them) until they press a button:
 * the press goes to Row-Bot as a labelled message with that button's
 * context, never as approval of anything.
 */
export function SurfaceView({
  json,
  blockId,
  stateKey,
  target,
  place = 'chat',
  onPress,
}: {
  json: string;
  blockId: string;
  /** Where what the person enters is kept: by reply and surface, so a pinned copy shares it. */
  stateKey: string;
  target: SurfaceTarget;
  /** Where it is shown: in its reply, opened bigger (the panel names it), or pinned on the Home Board. */
  place?: 'chat' | 'panel' | 'board';
  /** A press where the chat isn't open (the Board): the caller takes the person there and sends it. */
  onPress?: (press: SurfacePress) => void;
}) {
  const inPanel = place === 'panel';
  const spec = useMemo(() => readSurface(json), [json]);
  const controller = useOptionalRuntime()?.controller;
  const answers = useAnswerState(controller, target.conversationId);
  const saved = answers.surfaces?.[stateKey] as DataModel | undefined;
  const live = useSurfaceModel(stateKey);
  const latestBlock = useLatestSurfaceBlock(spec?.surfaceId ?? '');
  const sources = useSurfaceSources(
    target.conversationId,
    target.messageId,
    spec,
    // On the Board it stays current while Home is shown: read again, never asked of the model.
    place === 'board' ? BOARD_REFRESH : 0,
  );
  const board = useBoard();
  const ready = usePromptReady(target.conversationId);
  const notify = useNotify();
  const [pressed, setPressed] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef<DataModel | null>(null);
  const uid = useMemo(
    () => `surface-${blockId.slice(-8)}-${place}`,
    [blockId, place],
  );
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (ready) setPressed(null);
  }, [ready]);
  if (!spec) return <SurfaceUnreadable />;
  // What Row-Bot filled in on this computer wins over any older copy of it.
  const current = withSources(
    live ?? saved ?? spec.dataModel,
    sources?.values ?? {},
  );
  const save = (next: DataModel) => {
    // The sources' values are read again each time, never kept beside the chat.
    latest.current = withoutSources(next, spec);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      if (latest.current)
        void changeAnswerState(controller, target.conversationId, {
          block_id: stateKey,
          surface_data: latest.current,
        });
    }, SAVE_DELAY);
  };
  const context: Context = {
    byId: new Map(
      spec.components.map((component) => [component.id, component]),
    ),
    model: current,
    set: (path, value, scope) => {
      const next = writePath(current, path, value, scope);
      setSurfaceModel(stateKey, next);
      save(next);
    },
    press: (press) => {
      const surface = {
        message_id: target.messageId ?? '',
        surface_id: spec.surfaceId,
        name: press.name,
        source_component_id: press.componentId,
        context: press.context,
      };
      if (onPress && target.messageId) {
        onPress(surface);
        sendPromptWhenOpen(target.conversationId ?? '', press.label, {
          surface,
        });
        return;
      }
      if (
        !target.conversationId ||
        !target.messageId ||
        !sendPrompt(target.conversationId, press.label, { surface })
      ) {
        notify(
          'Row-Bot can’t take that right now. Try again when it’s ready.',
          'warning',
        );
        return;
      }
      setPressed(press.name);
    },
    // The Board takes the person to the chat, which sends it when it can.
    ready: place === 'board' || ready,
    pressed,
    uid,
    title: spec.title,
  };
  // A later reply drew this interface again: this copy is its history.
  const superseded = !!latestBlock && latestBlock !== blockId;
  const open =
    place === 'chat' && target.conversationId
      ? () =>
          requestSurfacePanel({
            conversationId: target.conversationId!,
            surfaceId: spec.surfaceId,
            title: spec.title,
          })
      : undefined;
  return (
    <section
      className="genui"
      aria-label={spec.title}
      data-surface-id={spec.surfaceId}
      data-in-panel={inPanel ? 'true' : undefined}
      data-superseded={superseded ? 'true' : undefined}
    >
      {(place === 'chat' || sources) && (
        <header className="genui-header">
          <span className="genui-title">
            {place === 'chat' ? spec.title : ''}
          </span>
          {superseded && (
            <span className="genui-superseded">Updated in a later reply</span>
          )}
          {sources && <PrivateMark spec={spec} sources={sources} />}
          {place !== 'board' && target.conversationId && target.messageId && (
            <PinButton
              pin={pinned(board, target.conversationId, spec.surfaceId)}
              onPin={() =>
                pinSurface(controller, {
                  conversation_id: target.conversationId!,
                  message_id: target.messageId!,
                  surface_id: spec.surfaceId,
                })
              }
            />
          )}
          {open && (
            <IconButton
              size="sm"
              variant="ghost"
              label="Open bigger"
              onClick={() => void open()}
            >
              <Maximize2 size={14} aria-hidden />
            </IconButton>
          )}
        </header>
      )}
      {sources && Object.keys(sources.problems).length > 0 && (
        <p className="genui-problems" role="status">
          <TriangleAlert size={14} aria-hidden />
          {[...new Set(Object.values(sources.problems))].join(' ')}
        </p>
      )}
      <Node id="root" scope={undefined} context={context} depth={0} />
    </section>
  );
}

/** Pin to Home, or unpin: the surface stays current on the Home Board. */
function PinButton({
  pin,
  onPin,
}: {
  pin: ReturnType<typeof pinned>;
  onPin: () => Promise<boolean>;
}) {
  const controller = useOptionalRuntime()?.controller;
  const notify = useNotify();
  const [busy, setBusy] = useState(false);
  const toggle = async () => {
    setBusy(true);
    try {
      if (pin) await unpinSurface(controller, pin.pin_id);
      else await onPin();
    } catch (cause) {
      notify(clientError(cause).message, 'warning');
    } finally {
      setBusy(false);
    }
  };
  return (
    <IconButton
      size="sm"
      variant="ghost"
      label={pin ? 'Unpin from Home' : 'Pin to Home'}
      pressed={!!pin}
      disabled={busy}
      onClick={() => void toggle()}
    >
      {pin ? <PinOff size={14} aria-hidden /> : <Pin size={14} aria-hidden />}
    </IconButton>
  );
}

/**
 * Which parts Row-Bot filled in on this computer, and Read again. The
 * values came from the person's own data and never went to the model.
 */
function PrivateMark({
  spec,
  sources,
}: {
  spec: SurfaceSpec;
  sources: SourceState;
}) {
  const words = sourceWords(spec);
  const label = `Filled in on this computer: ${words.join(', ')}. Row-Bot’s model didn’t see these values.`;
  return (
    <>
      <Hint label={label}>
        <span
          className="genui-private"
          tabIndex={0}
          aria-label={label}
          data-loading={sources.loading ? 'true' : undefined}
        >
          <Lock size={12} aria-hidden />
          {sources.loading ? 'Filling in…' : 'On this computer'}
        </span>
      </Hint>
      <IconButton
        size="sm"
        variant="ghost"
        label="Read again"
        disabled={sources.loading}
        onClick={sources.refresh}
      >
        <RefreshCw size={14} aria-hidden />
      </IconButton>
    </>
  );
}

function SurfaceUnreadable() {
  return (
    <p className="genui-unreadable">
      <Minus size={14} aria-hidden />
      This interface can’t be shown.
    </p>
  );
}
