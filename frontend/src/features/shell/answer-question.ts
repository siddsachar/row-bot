import type { ClientController } from '../../api/controller';
import type {
  ApprovalAnswer,
  ApprovalQuestion,
  ApprovalView,
  ClientState,
} from '../../api/types';
import { readPendingApprovalsNow } from './pending-approvals';
import { resolveApproval } from './resolve-approval';

/** The approval Row-Bot's turn in this conversation waits on, if it waits on one. */
export function waitingApproval(
  state: Pick<ClientState, 'projection'>,
): string | null {
  const generation = state.projection?.generation;
  return generation?.status === 'waiting_approval' && generation.approval_id
    ? generation.approval_id
    : null;
}

export type WaitingQuestion = {
  approvalId: string;
  view: ApprovalView;
  question: ApprovalQuestion;
};

/** That approval read from the server, when it is a card with exactly one question. */
export async function readQuestion(
  controller: ClientController,
  approvalId: string,
  signal?: AbortSignal,
): Promise<WaitingQuestion | null> {
  const view = await controller.approval(approvalId, signal);
  const questions = view.questions ?? [];
  return questions.length === 1
    ? { approvalId, view, question: questions[0] }
    : null;
}

/**
 * Answers a waiting one-question card the way the card does: with one of its
 * options, or the person's own words (a message typed while it waits, or the
 * answer above the page in the Browser pane). An answer is input, never an
 * approval.
 */
export async function answerQuestion(
  controller: ClientController,
  asked: WaitingQuestion,
  answer: ApprovalAnswer,
): Promise<void> {
  await resolveApproval(controller, asked.approvalId, asked.view, {
    decision: 'approve',
    answers: [answer],
  });
  readPendingApprovalsNow(controller.pendingApprovals);
}
