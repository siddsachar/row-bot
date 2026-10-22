import { MoreHorizontal } from 'lucide-react';
import { Button, Menu } from '../../ui/primitives';
import { Drawer } from '../../ui/overlays';
import './learning.css';

/**
 * One change to review (designs/learning-ux.md §4). Only its shape exists so far:
 * L3b fills it from tested changes. Counts always come with their denominator.
 */
export type ReviewChange = {
  title: string;
  /** "Lesson for document lookups". */
  kind: string;
  /** Why it was proposed, in plain words. */
  why: string;
  /** The text the change adds (a lesson) or the setting it changes. */
  change: string;
  /** The text was edited since it was tested: test again before using it. */
  edited?: boolean;
  results: {
    rows: { label: string; total: number; without: number; with: number }[];
    /** "Testing used 85K tokens · Small test, so expect some variation." */
    note: string;
  };
};

export default function ReviewSheet({
  open,
  onOpenChange,
  change,
  onUse,
  onTestAgain,
  onNotNow,
  onEdit,
  onSeeChats,
  onDontSuggest,
  onDetails,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  change: ReviewChange;
  onUse: () => void;
  onTestAgain: () => void;
  onNotNow: () => void;
  onEdit?: () => void;
  onSeeChats?: () => void;
  onDontSuggest: () => void;
  onDetails: () => void;
}) {
  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      modal
      title={change.title}
      description={change.kind}
      className="learning-review"
      closeLabel="Close review"
    >
      <div className="learning-review-body">
        <section aria-label="Why">
          <h3>Why</h3>
          <p>{change.why}</p>
          {onSeeChats && (
            <div>
              <Button variant="ghost" className="small" onClick={onSeeChats}>
                See the chats
              </Button>
            </div>
          )}
        </section>
        <section aria-label="What changes">
          <h3>What changes</h3>
          <blockquote className="learning-review-quote">
            {change.change}
          </blockquote>
          {change.edited && (
            <p role="status">Edited: test again before using.</p>
          )}
          {onEdit && (
            <div>
              <Button variant="ghost" className="small" onClick={onEdit}>
                Edit
              </Button>
            </div>
          )}
        </section>
        <section aria-label="Test result">
          <h3>Test result</h3>
          <table className="learning-results">
            <caption className="visually-hidden">
              Tasks passed without and with this change
            </caption>
            <thead>
              <tr>
                <th scope="col">Tasks</th>
                <th scope="col">Without</th>
                <th scope="col">With</th>
              </tr>
            </thead>
            <tbody>
              {change.results.rows.map((row) => (
                <tr key={row.label}>
                  <th scope="row">
                    {row.label} ({row.total})
                  </th>
                  <td data-label="Without">{row.without}</td>
                  <td data-label="With">{row.with} passed</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="learning-muted">{change.results.note}</p>
        </section>
      </div>
      <div className="learning-review-actions">
        {change.edited ? (
          <Button variant="primary" onClick={onTestAgain}>
            Test again
          </Button>
        ) : (
          <Button variant="primary" onClick={onUse}>
            Use this change
          </Button>
        )}
        <Button variant="ghost" onClick={onNotNow}>
          Not now
        </Button>
        <span className="learning-review-more">
          <Menu
            label="More for this change"
            iconOnly
            variant="ghost"
            className="icon-action"
            actions={[
              { label: 'Don’t suggest this again', onSelect: onDontSuggest },
              { label: 'Experiment details', onSelect: onDetails },
            ]}
          >
            <MoreHorizontal size={16} aria-hidden />
          </Menu>
        </span>
      </div>
    </Drawer>
  );
}
