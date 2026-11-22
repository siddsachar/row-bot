import { Laptop } from 'lucide-react';
import type { TaskSummary } from '../../api/types';
import { ModalTask } from '../../ui/overlays';
import { Toggle } from '../../ui/primitives';
import { approvalTag } from '../shell/approval-levels';
import TaskRun, { type TaskRunProps } from './TaskRun';
import { WorkflowLine, phoneSchedule, ranOnce } from './workflow-status';
import '../../ui/styles/phone-workflows.css';

/** What the sheet runs with: the run drawer's own reads and commands. */
export type WorkflowRunIo = Pick<
  TaskRunProps,
  'loadReview' | 'run' | 'loadHistory' | 'stop' | 'loadRun' | 'openConversation'
>;

/**
 * One workflow on a phone (C2, D11 A): its state and switch, Run now and
 * Open chat, its recent runs with their answers and what they wait for.
 * Editing stays on the computer, or is asked for in its chat.
 */
export default function PhoneWorkflowSheet({
  taskId,
  task,
  name,
  onClose,
  onToggle,
  toggling,
  io,
  onFinished,
}: {
  /** The open workflow, or null when the sheet is closed. */
  taskId: string | null;
  /** Its row, once the list has read it. */
  task: TaskSummary | null;
  /** Its name while the row is not read yet (a link carries only the id). */
  name: string;
  onClose: () => void;
  onToggle: (task: TaskSummary, enabled: boolean) => void;
  toggling: boolean;
  io: WorkflowRunIo;
  /** A run started or ended: the list re-reads. */
  onFinished: () => void;
}) {
  const title = task?.name || name || 'Workflow';
  return (
    <ModalTask
      open={taskId !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      kind="sheet"
      className="phone-workflow-sheet"
      title={title}
      description={
        task
          ? task.notify_only
            ? `${phoneSchedule(task)} · Reminder`
            : `${phoneSchedule(task)} · ${approvalTag(task.approval_mode)}`
          : ''
      }
    >
      {taskId !== null && (
        <div className="phone-workflow-sheet-body">
          {task && (
            <div className="phone-workflow-state">
              <span className="phone-workflow-state-line">
                <WorkflowLine task={task} schedule={false} />
              </span>
              {!ranOnce(task) && (
                <Toggle
                  label={`${task.enabled ? 'Turn off' : 'Turn on'} workflow: ${title}`}
                  checked={task.enabled}
                  disabled={toggling}
                  onChange={(event) => onToggle(task, event.target.checked)}
                />
              )}
            </div>
          )}
          <TaskRun
            key={taskId}
            layout="phone"
            taskId={taskId}
            chatId={task?.conversation_id}
            loadReview={io.loadReview}
            loadHistory={io.loadHistory}
            loadRun={io.loadRun}
            run={io.run}
            stop={io.stop}
            openConversation={io.openConversation}
            onFinished={onFinished}
          />
          <p className="phone-workflow-note">
            <Laptop size={16} aria-hidden />
            Change its messages or schedule on your computer, or ask in its
            chat.
          </p>
        </div>
      )}
    </ModalTask>
  );
}
