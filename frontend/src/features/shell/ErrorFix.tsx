import { useNavigate } from 'react-router-dom';
import type { ErrorAction } from '../../api/types';
import { useRuntime } from '../../runtime';
import { Button } from '../../ui/primitives';

/**
 * The one fix the error catalog names for an error (`api/errors.ts`):
 * Retry, Reconnect, Choose a model, Send now or Open <setting>. A fix whose
 * handler the surface does not have is left out rather than shown dead.
 */
export default function ErrorFix({
  action,
  retry,
  sendNow,
  chooseModel,
}: {
  action: ErrorAction;
  retry?: () => void;
  sendNow?: () => void;
  chooseModel?: () => void;
}) {
  const { controller } = useRuntime();
  const navigate = useNavigate();
  switch (action.kind) {
    case 'retry':
      return retry ? (
        <Button className="error-fix" onClick={retry}>
          Retry
        </Button>
      ) : null;
    case 'reconnect':
      return (
        <Button
          className="error-fix"
          onClick={() => void controller.reconnect()}
        >
          Reconnect
        </Button>
      );
    case 'choose_model':
      return (
        <Button
          className="error-fix"
          onClick={() =>
            chooseModel
              ? chooseModel()
              : navigate('/settings/models#default-model')
          }
        >
          Choose a model
        </Button>
      );
    case 'send_now':
      return sendNow ? (
        <Button className="error-fix" onClick={sendNow}>
          Send now
        </Button>
      ) : null;
    case 'open_setting':
      return (
        <Button className="error-fix" onClick={() => navigate(action.href)}>
          {action.label}
        </Button>
      );
  }
}
