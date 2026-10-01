import type { CSSProperties, ReactNode } from 'react';
import type { ArtifactPage } from '../../api/types';

/**
 * The page strip on the left of a multi-page design. Thumbnails render only
 * while the strip is on screen (a narrow panel hides it and keeps the dock's
 * page menu), so a hidden strip never loads pages.
 */
export default function DesignPageStrip({
  pages,
  currentId,
  pageLabel,
  disabled,
  onSelect,
  renderThumbnail,
  aspect,
}: {
  pages: ArtifactPage[];
  currentId: string;
  pageLabel: string;
  disabled: boolean;
  onSelect: (pageId: string) => void;
  renderThumbnail?: (pageId: string) => ReactNode;
  /** Canvas width over height, so thumbnails keep the page's shape. */
  aspect?: number;
}) {
  const shape =
    aspect && Number.isFinite(aspect)
      ? ({
          '--page-aspect': Math.min(2.5, Math.max(0.5, aspect)).toFixed(4),
        } as CSSProperties)
      : undefined;
  return (
    <nav className="design-pages" aria-label={`${pageLabel}s`} style={shape}>
      <ol>
        {pages.map((page) => {
          const current = page.id === currentId;
          return (
            <li key={page.id} data-current={current ? 'true' : undefined}>
              <div className="design-page-thumb" aria-hidden>
                {renderThumbnail ? (
                  renderThumbnail(page.id)
                ) : (
                  <span className="design-page-title">{page.title}</span>
                )}
              </div>
              <button
                type="button"
                className="design-page-hit"
                aria-current={current ? 'page' : undefined}
                disabled={disabled}
                title={page.title}
                onClick={() => onSelect(page.id)}
              >
                <span className="design-page-number">{page.index + 1}</span>
                <span className="visually-hidden">
                  {pageLabel} {page.index + 1}: {page.title}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
