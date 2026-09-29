import { useMemo } from 'react';
import { encode } from 'uqr';

export type QrCodeProps = {
  /** The text the code carries (a link). */
  value: string;
  /** What a screen reader hears, e.g. "QR code for the published link". */
  label: string;
  /** Rendered width and height in CSS pixels. */
  size?: number;
};

/**
 * A QR code drawn locally as one SVG path (bundled `uqr`, no network, no
 * HTML injection). Dark modules on a white quiet zone so phones read it in
 * either theme.
 */
export function QrCode({ value, label, size = 160 }: QrCodeProps) {
  const drawing = useMemo(() => {
    const code = encode(value, { ecc: 'M', border: 2 });
    let path = '';
    code.data.forEach((row, y) =>
      row.forEach((dark, x) => {
        if (dark) path += `M${x} ${y}h1v1h-1z`;
      }),
    );
    return { path, size: code.size };
  }, [value]);
  return (
    <svg
      className="qr-code"
      role="img"
      aria-label={label}
      width={size}
      height={size}
      viewBox={`0 0 ${drawing.size} ${drawing.size}`}
      shapeRendering="crispEdges"
    >
      <rect width={drawing.size} height={drawing.size} fill="#fff" />
      <path d={drawing.path} fill="#000" />
    </svg>
  );
}
