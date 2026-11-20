import { ChevronRight } from 'lucide-react';
import { useRef, useState } from 'react';
import type { ContextUsageView } from '../../api/types';
import {
  ContextDetailsPopover,
  describeContextUsage,
  Ring,
} from './ContextUsage';
import '../../ui/styles/phone-composer.css';

/**
 * How full the chat's context is, as one line for Conversation details on a
 * phone (B2): the ring and "43% of context · compacts at 80%". Once measured,
 * a tap opens what fills it, as the + menu's line did before.
 */
export default function ContextLine({
  usage,
}: {
  usage?: ContextUsageView | null;
}) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const context = describeContextUsage(usage);
  const measured = !!usage && usage.state !== 'unknown';
  const ring = <Ring percent={context.percent} threshold={context.threshold} />;
  return (
    <section
      className="context-rail-section context-line"
      aria-labelledby="context-line-heading"
    >
      <h3 id="context-line-heading">Context</h3>
      {measured ? (
        <>
          <button
            ref={button}
            type="button"
            className="context-line-row"
            aria-haspopup="dialog"
            aria-expanded={open}
            onClick={() => setOpen(true)}
          >
            {ring}
            <span className="context-line-text">{context.text}</span>
            <ChevronRight size={16} aria-hidden />
          </button>
          <ContextDetailsPopover
            usage={usage}
            open={open}
            onOpenChange={setOpen}
            anchor={button}
          />
        </>
      ) : (
        <p className="context-line-row">
          {ring}
          <span className="context-line-text">{context.text}</span>
        </p>
      )}
    </section>
  );
}
