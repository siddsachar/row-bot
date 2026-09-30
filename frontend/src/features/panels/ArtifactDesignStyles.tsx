import {
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import {
  AlignCenter,
  AlignJustify,
  AlignLeft,
  AlignRight,
  Crop,
  Image as ImageGlyph,
  Minus,
  Plus,
  Replace,
  Upload,
} from 'lucide-react';
import { Hint, IconButton, Input, Menu, Segmented } from '../../ui/primitives';
import ArtifactFontPicker from './ArtifactFontPicker';
import { useThumbnails } from './ArtifactLogoPicker';
import type { DesignCatalog } from './artifact-design-catalog';
import type {
  DesignBrand,
  DesignControlItem,
  DesignControlsProps,
  DesignElementView,
} from './ArtifactDesignControls';
import {
  brandSwatches,
  currentStyle,
  hexOf,
  numberOf,
  swatchOf,
  type BrandSwatch,
  type DesignLook,
} from './artifact-design-values';

/** A stepper or a picked colour saves this long after the last change. */
export const SETTLE_DELAY = 600;

/** One inspector row: a short label on the left, its control on the right. */
export function DesignRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="design-row">
      <span className="design-row-label" aria-hidden>
        {label}
      </span>
      <div className="design-row-control">{children}</div>
    </div>
  );
}

export function DesignGroup({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="design-group" aria-label={title}>
      <h4 className="design-group-title">
        {title}
        {action}
      </h4>
      {children}
    </section>
  );
}

const round = (value: number, step: number) =>
  step < 1 ? Math.round(value * 100) / 100 : Math.round(value);

/** − value + with a typed value too; empty shows Auto (nothing saved). */
export function DesignStepper({
  label,
  value,
  unit,
  step = 1,
  min = 0,
  max = 2000,
  fallback,
  disabled,
  onChange,
}: {
  label: string;
  value: number | null;
  unit?: string;
  step?: number;
  min?: number;
  max?: number;
  /** Where − and + start from when the value is Auto. */
  fallback?: number;
  disabled: boolean;
  onChange: (value: number, settle: boolean) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const base = value ?? fallback ?? null;
  const set = (next: number, settle: boolean) =>
    onChange(Math.min(max, Math.max(min, round(next, step))), settle);
  const name = label.toLowerCase();
  return (
    <span className="design-stepper">
      <IconButton
        size="sm"
        label={`Decrease ${name}`}
        disabled={disabled || base === null || base <= min}
        onClick={() => set(base! - step, true)}
      >
        <Minus size={14} aria-hidden />
      </IconButton>
      <input
        className="design-stepper-input"
        aria-label={label}
        inputMode="decimal"
        placeholder="Auto"
        disabled={disabled}
        value={draft ?? (value === null ? '' : String(round(value, step)))}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          if (draft === null) return;
          const typed = Number(
            draft.replace(',', '.').replace(/[a-z%]+$/i, ''),
          );
          setDraft(null);
          if (draft.trim() && Number.isFinite(typed)) set(typed, false);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur();
          if (event.key === 'Escape') setDraft(null);
          if (
            (event.key === 'ArrowUp' || event.key === 'ArrowDown') &&
            base !== null
          ) {
            event.preventDefault();
            set(base + (event.key === 'ArrowUp' ? step : -step), true);
          }
        }}
      />
      {unit && (
        <span className="design-stepper-unit" aria-hidden>
          {unit}
        </span>
      )}
      <IconButton
        size="sm"
        label={`Increase ${name}`}
        disabled={disabled || base === null || base >= max}
        onClick={() => set(base! + step, true)}
      >
        <Plus size={14} aria-hidden />
      </IconButton>
    </span>
  );
}

/** Brand colours as swatches, the current one marked, plus any colour. */
export function DesignSwatches({
  label,
  value,
  swatches,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  swatches: BrandSwatch[];
  disabled: boolean;
  onChange: (value: string, settle: boolean) => void;
}) {
  const chosen = swatchOf(value, swatches);
  const custom = value && !chosen ? hexOf(value) : null;
  return (
    <div className="design-swatches" role="group" aria-label={label}>
      {swatches.map((swatch) => (
        <Hint
          key={swatch.value}
          label={`${swatch.name} · ${swatch.hex.toUpperCase()}`}
        >
          <button
            type="button"
            className="design-swatch"
            aria-label={swatch.name}
            aria-pressed={chosen?.value === swatch.value}
            disabled={disabled}
            style={{ '--swatch': swatch.hex } as CSSProperties}
            onClick={() => onChange(swatch.value, false)}
          />
        </Hint>
      ))}
      {custom && (
        <button
          type="button"
          className="design-swatch"
          aria-label={`Custom ${custom.toUpperCase()}`}
          aria-pressed
          disabled={disabled}
          style={{ '--swatch': custom } as CSSProperties}
        />
      )}
      <Hint label="Another colour">
        <label className="design-swatch design-swatch-more">
          <Plus size={12} aria-hidden />
          <input
            type="color"
            aria-label={`${label}: another colour`}
            disabled={disabled}
            value={custom ?? chosen?.hex ?? '#000000'}
            onChange={(event) => onChange(event.target.value, true)}
          />
        </label>
      </Hint>
    </div>
  );
}

type Style = (patch: Record<string, string>, settle?: boolean) => void;

type Values = {
  styles: Record<string, string>;
  look?: DesignLook;
  busy: boolean;
  onStyle: Style;
};

function number(key: string, { styles, look }: Values) {
  return numberOf(currentStyle(key, styles, look));
}

function Stepper({
  label,
  styleKey,
  values,
  unit = 'px',
  ...rest
}: {
  label: string;
  styleKey: string;
  values: Values;
  unit?: string;
  step?: number;
  min?: number;
  max?: number;
  fallback?: number;
}) {
  return (
    <DesignRow label={label}>
      <DesignStepper
        label={label}
        unit={unit}
        value={number(styleKey, values)}
        disabled={values.busy}
        onChange={(value, settle) =>
          values.onStyle(
            { [styleKey]: unit ? `${value}${unit}` : String(value) },
            settle,
          )
        }
        {...rest}
      />
    </DesignRow>
  );
}

function Colour({
  label,
  styleKey,
  values,
  swatches,
}: {
  label: string;
  styleKey: string;
  values: Values;
  swatches: BrandSwatch[];
}) {
  return (
    <DesignRow label={label}>
      <DesignSwatches
        label={label}
        value={currentStyle(styleKey, values.styles, values.look)}
        swatches={swatches}
        disabled={values.busy}
        onChange={(value, settle) =>
          values.onStyle({ [styleKey]: value }, settle)
        }
      />
    </DesignRow>
  );
}

/** One of a few choices; a value none of them matches leaves none chosen. */
function Choice({
  label,
  value,
  options,
  values,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly { value: string; label: string; icon?: ReactNode }[];
  values: Values;
  onChange: (value: string) => void;
}) {
  return (
    <DesignRow label={label}>
      <Segmented
        size="sm"
        label={label}
        className="design-choice"
        value={value}
        onChange={onChange}
        options={options.map((option) => ({
          ...option,
          hideLabel: Boolean(option.icon),
          disabled: values.busy,
        }))}
      />
    </DesignRow>
  );
}

/** Width as Fill, Fixed (with a stepper) or Fit; unset shows none chosen. */
function Width({
  values,
  fit,
}: {
  values: Values;
  /** The Fit option's words ("Fit text"); none for pictures. */
  fit?: string;
}) {
  const width = values.styles.width ?? '';
  const mode =
    width === '100%'
      ? 'fill'
      : width === 'fit-content' || width === 'auto'
        ? 'fit'
        : numberOf(width) !== null
          ? 'fixed'
          : '';
  const measured = numberOf(values.look?.width);
  return (
    <>
      <Choice
        label="Width"
        value={mode}
        values={values}
        options={[
          { value: 'fill', label: 'Fill' },
          { value: 'fixed', label: 'Fixed' },
          ...(fit ? [{ value: 'fit', label: fit }] : []),
        ]}
        onChange={(next) =>
          values.onStyle({
            width:
              next === 'fill'
                ? '100%'
                : next === 'fit'
                  ? 'fit-content'
                  : `${Math.round(measured ?? 240)}px`,
          })
        }
      />
      {mode === 'fixed' && (
        <Stepper label="Fixed width" styleKey="width" values={values} />
      )}
    </>
  );
}

const WEIGHTS = [
  { value: '400', label: 'Regular' },
  { value: '500', label: 'Medium' },
  { value: '700', label: 'Bold' },
] as const;
const ALIGN = [
  { value: 'left', label: 'Left', icon: <AlignLeft size={15} aria-hidden /> },
  {
    value: 'center',
    label: 'Centre',
    icon: <AlignCenter size={15} aria-hidden />,
  },
  {
    value: 'right',
    label: 'Right',
    icon: <AlignRight size={15} aria-hidden />,
  },
  {
    value: 'justify',
    label: 'Justify',
    icon: <AlignJustify size={15} aria-hidden />,
  },
] as const;
const FITS = [
  { value: 'cover', label: 'Fill' },
  { value: 'contain', label: 'Fit' },
  { value: 'fill', label: 'Stretch' },
] as const;
const CROPS = [
  ['center', 'Centre'],
  ['top', 'Top'],
  ['bottom', 'Bottom'],
  ['left', 'Left'],
  ['right', 'Right'],
] as const;
const BORDERS = [
  { value: 'none', label: 'None' },
  { value: 'solid', label: 'Solid' },
  { value: 'dashed', label: 'Dashed' },
] as const;

export type ImageActions = {
  /** The design's own pictures, for Replace. */
  images: DesignControlItem[] | null;
  thumbnail: DesignControlsProps['thumbnail'];
  onReplace: (assetId: string) => void;
  onUpload: (file: File) => void;
  onDescribe: (alt: string) => void;
};

/** How the canvas reports each crop (object-position) choice. */
const POSITIONS: Record<string, string> = {
  '50% 50%': 'center',
  '50% 0%': 'top',
  '50% 100%': 'bottom',
  '0% 50%': 'left',
  '100% 50%': 'right',
};

function ImageGroup({
  element,
  values,
  actions,
}: {
  element: DesignElementView;
  values: Values;
  actions: ImageActions;
}) {
  const shown = useMemo(
    () => (element.asset_id ? [{ id: element.asset_id }] : []),
    [element.asset_id],
  );
  const picture = useThumbnails(shown, actions.thumbnail)[element.asset_id];
  const file = useRef<HTMLInputElement>(null);
  const [alt, setAlt] = useState<string | null>(null);
  const fit = currentStyle('object-fit', values.styles, values.look);
  const crop = currentStyle('object-position', values.styles, values.look);
  const cropValue = POSITIONS[crop] ?? (crop || 'center');
  const cropWord =
    CROPS.find(([value]) => value === cropValue)?.[1] ?? 'Custom';
  const images = (actions.images ?? []).filter((item) => item.kind === 'image');
  return (
    <DesignGroup title="Image">
      <div className="design-image-row">
        <span className="design-image-thumb" aria-hidden>
          {picture ? (
            <img src={picture} alt="" draggable={false} />
          ) : (
            <ImageGlyph size={18} />
          )}
        </span>
        <Menu
          label="Replace…"
          className="design-image-replace"
          disabled={values.busy}
          actions={[
            ...images.map((image) => ({
              label: image.label,
              selected: image.id === element.asset_id,
              onSelect: () => actions.onReplace(image.id),
            })),
            {
              label: 'Upload an image…',
              icon: <Upload size={16} aria-hidden />,
              separatorBefore: images.length > 0,
              onSelect: () => file.current?.click(),
            },
          ]}
        >
          <Replace size={14} aria-hidden />
          <span>Replace…</span>
        </Menu>
        <input
          ref={file}
          type="file"
          hidden
          aria-label="Image file"
          accept=".png,.jpg,.jpeg,.webp,.gif,.svg"
          onChange={(event) => {
            const chosen = event.target.files?.[0];
            event.target.value = '';
            if (chosen) actions.onUpload(chosen);
          }}
        />
      </div>
      <Choice
        label="Fit"
        value={fit}
        options={FITS}
        values={values}
        onChange={(value) => values.onStyle({ 'object-fit': value })}
      />
      <DesignRow label="Crop">
        <Menu
          label={`Crop: ${cropWord}. Choose which part shows`}
          className="design-crop"
          disabled={values.busy || (fit !== '' && fit !== 'cover')}
          actions={CROPS.map(([value, word]) => ({
            label: word,
            selected: word === cropWord,
            onSelect: () => values.onStyle({ 'object-position': value }),
          }))}
        >
          <Crop size={14} aria-hidden />
          <span>{cropWord}</span>
        </Menu>
      </DesignRow>
      <Stepper
        label="Corners"
        styleKey="border-radius"
        values={values}
        fallback={0}
      />
      <DesignRow label="Description">
        <Input
          aria-label="Description"
          placeholder="What the picture shows"
          maxLength={512}
          disabled={values.busy}
          value={alt ?? element.alt}
          onChange={(event) => setAlt(event.target.value)}
          onBlur={() => {
            if (alt !== null && alt.trim() !== element.alt)
              actions.onDescribe(alt.trim());
            setAlt(null);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') event.currentTarget.blur();
            if (event.key === 'Escape') setAlt(null);
          }}
        />
      </DesignRow>
    </DesignGroup>
  );
}

/**
 * The selected element's own controls (B247): text gets font, size, weight,
 * colour and alignment; a picture replace, fit and crop; a shape fill,
 * corners and border; every kind its size and spacing. No CSS text.
 */
export default function ArtifactDesignStyles({
  element,
  styles,
  look,
  brand,
  fonts,
  busy,
  onStyle,
  image,
}: {
  element: DesignElementView;
  styles: Record<string, string>;
  look?: DesignLook;
  brand: DesignBrand;
  fonts: DesignCatalog;
  busy: boolean;
  onStyle: Style;
  image: ImageActions;
}) {
  const values: Values = { styles, look, busy, onStyle };
  const swatches = brandSwatches(brand);
  const weight = currentStyle('font-weight', styles, look);
  const align = currentStyle('text-align', styles, look);
  const border = currentStyle('border-style', styles, look);
  const opacity = numberOf(currentStyle('opacity', styles, look));
  if (element.kind === 'text')
    return (
      <>
        <DesignGroup title="Text">
          <DesignRow label="Font">
            <ArtifactFontPicker
              label="Font"
              value={currentStyle('font-family', styles, look)}
              catalog={fonts}
              disabled={busy}
              onChange={(font) => onStyle({ 'font-family': font })}
            />
          </DesignRow>
          <Stepper
            label="Size"
            styleKey="font-size"
            values={values}
            min={1}
            fallback={16}
          />
          <Choice
            label="Weight"
            value={weight}
            options={WEIGHTS}
            values={values}
            onChange={(value) => onStyle({ 'font-weight': value })}
          />
          <Colour
            label="Colour"
            styleKey="color"
            values={values}
            swatches={swatches}
          />
          <Choice
            label="Align"
            value={align}
            options={ALIGN}
            values={values}
            onChange={(value) => onStyle({ 'text-align': value })}
          />
          <Stepper
            label="Line height"
            styleKey="line-height"
            values={values}
            unit=""
            step={0.1}
            min={0.5}
            max={5}
            fallback={1.2}
          />
          <Stepper
            label="Letter spacing"
            styleKey="letter-spacing"
            values={values}
            step={0.5}
            min={-20}
            max={100}
            fallback={0}
          />
        </DesignGroup>
        <DesignGroup title="Layout">
          <Width values={values} fit="Fit text" />
          <Stepper
            label="Space around"
            styleKey="margin"
            values={values}
            fallback={0}
          />
        </DesignGroup>
      </>
    );
  if (element.kind === 'image')
    return (
      <>
        <ImageGroup element={element} values={values} actions={image} />
        <DesignGroup title="Layout">
          <Width values={values} />
          <Stepper label="Height" styleKey="height" values={values} />
        </DesignGroup>
      </>
    );
  return (
    <>
      <DesignGroup title={element.kind === 'shape' ? 'Shape' : 'Box'}>
        <Colour
          label="Fill"
          styleKey="background-color"
          values={values}
          swatches={swatches}
        />
        <Stepper
          label="Corners"
          styleKey="border-radius"
          values={values}
          fallback={0}
        />
        <Choice
          label="Border"
          value={border}
          options={BORDERS}
          values={values}
          onChange={(value) => onStyle({ 'border-style': value })}
        />
        {border !== '' && border !== 'none' && (
          <>
            <Stepper
              label="Border width"
              styleKey="border-width"
              values={values}
              fallback={1}
            />
            <Colour
              label="Border colour"
              styleKey="border-color"
              values={values}
              swatches={swatches}
            />
          </>
        )}
        <DesignRow label="Opacity">
          <DesignStepper
            label="Opacity"
            unit="%"
            step={5}
            max={100}
            value={opacity === null ? null : Math.round(opacity * 100)}
            fallback={100}
            disabled={busy}
            onChange={(value, settle) =>
              onStyle({ opacity: String(Math.round(value) / 100) }, settle)
            }
          />
        </DesignRow>
      </DesignGroup>
      <DesignGroup title="Layout">
        <Width values={values} fit="Fit" />
        <Stepper
          label="Space inside"
          styleKey="padding"
          values={values}
          fallback={0}
        />
        {element.kind === 'layout' && (
          <Stepper label="Gap" styleKey="gap" values={values} fallback={0} />
        )}
      </DesignGroup>
    </>
  );
}
