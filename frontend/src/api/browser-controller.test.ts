import { afterEach, describe, expect, it } from 'vitest';
import {
  validateWire,
  type BrowserReview,
  type BrowserReviewRequest,
} from '../../../contracts/client-platform/v1/typescript/client';
import { ClientController } from './controller';
import { FixtureTransport } from './fixtures';

class BrowserReviewTransport extends FixtureTransport {
  readonly reviews: Array<{
    conversation: string;
    body: BrowserReviewRequest;
  }> = [];

  async reviewBrowserControl(
    conversation: string,
    body: BrowserReviewRequest,
  ): Promise<BrowserReview> {
    this.reviews.push({ conversation, body });
    return {
      schema_version: 1,
      action: body.action,
      conversation_id: conversation,
      revision: body.payload.revision,
      policy_action: body.action,
      policy_decision: 'allow',
      policy_reason: 'Fixture browser review',
      approval_required: true,
      origin_and_path: 'https://example.test/path',
      query_present: false,
      disclosures: [],
      action_digest: '0'.repeat(64),
      nonce: 'fixture-browser-review-nonce',
    };
  }
}

const clients: ClientController[] = [];

afterEach(() => {
  clients.splice(0).forEach((client) => client.dispose());
});

describe('browser review requests', () => {
  it('sends action-discriminated navigate and revision-only bodies', async () => {
    const transport = new BrowserReviewTransport();
    const client = new ClientController(transport);
    const navigateRevision = '1'.repeat(64);
    const backRevision = '2'.repeat(64);
    clients.push(client);
    await client.start();

    await client.reviewBrowserControl('conversation-a', 'browser.navigate', {
      revision: navigateRevision,
      url: 'https://example.test/path',
    });
    await client.reviewBrowserControl('conversation-a', 'browser.back', {
      revision: backRevision,
    });

    expect(transport.reviews).toEqual([
      {
        conversation: 'conversation-a',
        body: {
          action: 'browser.navigate',
          type: 'browser.navigate',
          payload: {
            revision: navigateRevision,
            url: 'https://example.test/path',
          },
        },
      },
      {
        conversation: 'conversation-a',
        body: {
          action: 'browser.back',
          type: 'browser.back',
          payload: { revision: backRevision },
        },
      },
    ]);
  });

  it('keeps generated validation for action-specific payloads', () => {
    expect(() =>
      validateWire<BrowserReviewRequest>('BrowserReviewRequest', {
        action: 'browser.navigate',
        type: 'browser.navigate',
        payload: { revision: '3'.repeat(64) },
      }),
    ).toThrow('protocol_incompatible');
  });
});
