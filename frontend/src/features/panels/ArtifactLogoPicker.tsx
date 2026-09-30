import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowDownLeft,
  ArrowDownRight,
  ArrowUpLeft,
  ArrowUpRight,
  Image as ImageGlyph,
  ImageOff,
  Upload,
} from 'lucide-react';
import { Button, IconButton, Segmented } from '../../ui/primitives';
import type {
  DesignBrand,
  DesignControlItem,
  DesignControlsProps,
} from './ArtifactDesignControls';
import { useDesignCatalog } from './artifact-design-catalog';

export type LogoFields = Pick<
  DesignBrand,
  | 'logo_asset_id'
  | 'logo_mode'
  | 'logo_scope'
  | 'logo_position'
  | 'logo_max_height'
  | 'logo_padding'
>;

const PLACEMENTS = [
  ['top_left', 'Top left', ArrowUpLeft],
  ['top_right', 'Top right', ArrowUpRight],
  ['bottom_left', 'Bottom left', ArrowDownLeft],
  ['bottom_right', 'Bottom right', ArrowDownRight],
] as const;
/** Logo height in pixels (the server allows 24–240). */
const SIZES = [
  ['48', 'Small'],
  ['72', 'Medium'],
  ['120', 'Large'],
] as const;
/** Space around the logo in pixels (the server allows 0–160). */
const SPACING = [
  ['12', 'Tight'],
  ['24', 'Normal'],
  ['48', 'Roomy'],
] as const;

/**
 * Thumbnails of the design's pictures as object URLs, each read once and
 * freed when the picker closes. A picture that can't be read keeps the
 * glyph; its name still says which one it is.
 */
export function useThumbnails(
  images: readonly Pick<DesignControlItem, 'id'>[],
  thumbnail: DesignControlsProps['thumbnail'],
) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const asked = useRef(new Set<string>());
  const made = useRef<string[]>([]);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    const owned = made.current;
    const requested = asked.current;
    return () => {
      mounted.current = false;
      owned.forEach((url) => URL.revokeObjectURL(url));
      owned.length = 0;
      requested.clear();
    };
  }, []);
  useEffect(() => {
    for (const image of images) {
      if (asked.current.has(image.id)) continue;
      asked.current.add(image.id);
      thumbnail(image.id).then(
        (blob) => {
          if (!mounted.current) return;
          const url = URL.createObjectURL(blob);
          made.current.push(url);
          setUrls((all) => ({ ...all, [image.id]: url }));
        },
        () => undefined,
      );
    }
  }, [images, thumbnail]);
  return urls;
}

/**
 * The brand logo as pictures, not IDs: "No logo", the design's images and
 * "Upload logo…", then where, how big and on which pages, as simple choices.
 */
export default function ArtifactLogoPicker({
  logo,
  load,
  thumbnail,
  pageId,
  resourceRevision,
  disabled,
  onChange,
  onUpload,
}: {
  logo: LogoFields;
  load: DesignControlsProps['load'];
  thumbnail: DesignControlsProps['thumbnail'];
  pageId: string;
  resourceRevision: string;
  disabled: boolean;
  onChange: (change: Partial<LogoFields>) => void;
  onUpload: (file: File) => void;
}) {
  const assets = useDesignCatalog(
    load,
    pageId,
    'assets',
    true,
    resourceRevision,
  );
  const found = assets.items;
  const images = useMemo(
    () => (found ?? []).filter((item) => item.kind === 'image'),
    [found],
  );
  const urls = useThumbnails(images, thumbnail);
  const file = useRef<HTMLInputElement>(null);
  return (
    <div className="design-logo-picker">
      <div className="design-logo-row">
        <Segmented
          label="Logo"
          className="design-logo-choices"
          value={logo.logo_asset_id}
          onChange={(value) => onChange({ logo_asset_id: value })}
          options={[
            {
              value: '',
              label: 'No logo',
              icon: <ImageOff size={16} aria-hidden />,
              hideLabel: true,
              disabled,
            },
            ...images.map((image) => ({
              value: image.id,
              label: image.label,
              icon: urls[image.id] ? (
                <img src={urls[image.id]} alt="" draggable={false} />
              ) : (
                <ImageGlyph size={16} aria-hidden />
              ),
              hideLabel: true,
              disabled,
            })),
          ]}
        />
        <IconButton
          label="Upload logo…"
          disabled={disabled}
          onClick={() => file.current?.click()}
        >
          <Upload size={15} aria-hidden />
        </IconButton>
        <input
          ref={file}
          type="file"
          hidden
          aria-label="Logo file"
          accept=".png,.jpg,.jpeg,.webp,.gif,.svg"
          onChange={(event) => {
            const chosen = event.target.files?.[0];
            event.target.value = '';
            if (chosen) onUpload(chosen);
          }}
        />
      </div>
      {assets.failed && (
        <p className="muted design-logo-note">
          The design&apos;s pictures couldn&apos;t be read.{' '}
          <Button variant="ghost" onClick={assets.retry}>
            Try again
          </Button>
        </p>
      )}
      {logo.logo_asset_id && (
        <div className="design-logo-options">
          <span className="design-logo-caption" aria-hidden>
            Placement
          </span>
          <Segmented
            size="sm"
            label="Logo placement"
            value={logo.logo_position}
            onChange={(value) => onChange({ logo_position: value })}
            options={PLACEMENTS.map(([value, label, Icon]) => ({
              value,
              label,
              icon: <Icon size={14} aria-hidden />,
              hideLabel: true,
              disabled,
            }))}
          />
          <span className="design-logo-caption" aria-hidden>
            Size
          </span>
          <Segmented
            size="sm"
            label="Logo size"
            value={String(logo.logo_max_height)}
            onChange={(value) => onChange({ logo_max_height: Number(value) })}
            options={SIZES.map(([value, label]) => ({
              value,
              label,
              disabled,
            }))}
          />
          <span className="design-logo-caption" aria-hidden>
            Spacing
          </span>
          <Segmented
            size="sm"
            label="Logo spacing"
            value={String(logo.logo_padding)}
            onChange={(value) => onChange({ logo_padding: Number(value) })}
            options={SPACING.map(([value, label]) => ({
              value,
              label,
              disabled,
            }))}
          />
          <span className="design-logo-caption" aria-hidden>
            Pages
          </span>
          <Segmented
            size="sm"
            label="Logo pages"
            value={logo.logo_scope}
            onChange={(value) => onChange({ logo_scope: value })}
            options={[
              { value: 'all', label: 'All pages', disabled },
              { value: 'first', label: 'First page', disabled },
            ]}
          />
          <span className="design-logo-caption" aria-hidden>
            Placing
          </span>
          <Segmented
            size="sm"
            label="Logo mode"
            value={logo.logo_mode}
            onChange={(value) => onChange({ logo_mode: value })}
            options={[
              { value: 'auto', label: 'Automatic', disabled },
              { value: 'manual', label: 'Manual', disabled },
            ]}
          />
        </div>
      )}
    </div>
  );
}
