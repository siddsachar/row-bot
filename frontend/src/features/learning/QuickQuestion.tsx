import { useState } from 'react';
import { MessageCircleQuestionMark, X } from 'lucide-react';
import { clientError } from '../../api/errors';
import type { LearningQuickQuestion } from '../../api/types';
import { Button, EntityList, EntityRow, IconButton } from '../../ui/primitives';
import { useNotify } from '../../ui/overlays';
import type { LearningIO } from './learning-state';
import { kindLabel } from './words';

/**
 * Overview › Needs you: the quick question (designs §3). At most one a day, two answers to tap,
 * ✕ never asks it again. Nothing waits on it.
 */
export function QuickQuestion({
  io,
  question,
}: {
  io: LearningIO;
  question: LearningQuickQuestion | null | undefined;
}) {
  const notify = useNotify();
  const [busy, setBusy] = useState(false);
  if (!question) return null;
  const send = async (
    action: 'answer_question' | 'dismiss_question',
    answer = '',
  ) => {
    setBusy(true);
    try {
      notify((await io.run(action, { item_id: question.id, answer })).summary);
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    } finally {
      setBusy(false);
    }
  };
  return (
    <EntityList label="Quick question">
      <EntityRow
        className="learning-question"
        title={`Quick question: ${question.question}`}
        icon={<MessageCircleQuestionMark size={16} />}
        meta={question.kind === 'other' ? undefined : kindLabel(question.kind)}
        action={
          <span className="learning-question-actions">
            {question.answers.map((answer) => (
              <Button
                key={answer}
                className="small"
                disabled={busy}
                onClick={() => void send('answer_question', answer)}
              >
                {answer}
              </Button>
            ))}
            <IconButton
              size="sm"
              label="Don’t ask this again"
              disabled={busy}
              onClick={() => void send('dismiss_question')}
            >
              <X size={15} />
            </IconButton>
          </span>
        }
      />
    </EntityList>
  );
}
