import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useRuntime } from '../../runtime';
import { clientError } from '../../api/errors';
import type {
  IntegrationEntryPage,
  IntegrationResolveRequest,
} from '../../api/types';
import { Button, Field, Input, Select } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import { itemHref } from './parts';

/** Paste a link (its type is detected) or pick a .zip, .skill or .mcpb file; nothing is fetched or run yet. */
export default function AddFromLink({
  open,
  kind,
  onClose,
}: {
  open: boolean;
  kind: 'app' | 'skill';
  onClose: () => void;
}) {
  const { controller } = useRuntime();
  const navigate = useNavigate();
  const [link, setLink] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [type, setType] = useState<IntegrationResolveRequest['kind']>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const page: IntegrationEntryPage = file
        ? await controller.uploadIntegration(file, file.name)
        : await controller.resolveIntegration({
            reference: link.trim(),
            kind: type,
          });
      onClose();
      navigate(itemHref(page.items[0], page.revision));
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <ModalTask
      open={open}
      onOpenChange={(value) => !value && onClose()}
      title="Add from link or file"
      description={`Row-Bot shows what it found before anything is added.`}
    >
      <form className="stack" onSubmit={(event) => void submit(event)}>
        <Field
          label="Link"
          hint="An app's address, a GitHub repository, or a skill page."
        >
          <Input
            type="url"
            inputMode="url"
            data-initial-focus
            value={link}
            disabled={Boolean(file)}
            maxLength={2048}
            placeholder={
              kind === 'skill'
                ? 'https://github.com/…/SKILL.md'
                : 'https://mcp.example.com/mcp'
            }
            onChange={(event) => setLink(event.target.value)}
          />
        </Field>
        {!file && link.trim() && (
          <Field label="This is">
            <Select
              value={type}
              onChange={(event) => setType(event.target.value as typeof type)}
            >
              <option value="">Detect automatically</option>
              <option value="mcp">An app connection</option>
              <option value="skill">A skill</option>
              <option value="plugin">A package</option>
            </Select>
          </Field>
        )}
        <Field
          label="Or choose a file"
          hint=".zip or .skill for skills and packages; .mcpb bundles arrive in a later update."
        >
          <Input
            type="file"
            accept=".zip,.skill,.mcpb"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </Field>
        {error && <p role="alert">{error}</p>}
        <div className="app-dialog-actions">
          <Button onClick={onClose}>Cancel</Button>
          <Button
            type="submit"
            variant="primary"
            disabled={busy || (!file && !link.trim())}
          >
            Continue
          </Button>
        </div>
      </form>
    </ModalTask>
  );
}
