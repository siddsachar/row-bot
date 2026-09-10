import glyph from '../assets/row_bot_glyph_256.png';

/** The existing local Row-Bot artwork, bundled and hashed with this client. */
export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? 'brand-compact' : ''}`}>
      <img className="brand-glyph" src={glyph} alt="" width={40} height={40} />
      <span className="brand-name">Row-Bot</span>
    </div>
  );
}
