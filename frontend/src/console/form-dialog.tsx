import { useState, type FormEvent, type ReactNode } from 'react';
import { Button } from '../ui/primitives';
import { ModalTask } from '../ui/overlays';
import { problem } from './words';

/**
 * An add or edit form in a dialog: its fields, Cancel and one primary Save.
 * `onSubmit` sends the change; while it runs the form waits, and a refusal
 * shows in plain words above the buttons.
 */
export function FormDialog({
  title,
  description,
  submitLabel,
  onClose,
  onSubmit,
  children,
}: {
  title: string;
  description: string;
  submitLabel: string;
  onClose: () => void;
  onSubmit: () => Promise<void>;
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit();
      onClose();
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  }
  return (
    <ModalTask
      open
      title={title}
      description={description}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <form className="console-form" onSubmit={(event) => void submit(event)}>
        {children}
        {error !== null && (
          <p className="console-form-error" role="alert">
            {problem(error)}
          </p>
        )}
        <footer className="console-form-actions">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Saving…' : submitLabel}
          </Button>
        </footer>
      </form>
    </ModalTask>
  );
}
