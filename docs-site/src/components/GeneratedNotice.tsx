import React from 'react';

type GeneratedNoticeProps = {
  source?: string;
};

export default function GeneratedNotice(_props: GeneratedNoticeProps): JSX.Element {
  return (
    <aside className="rowBotGeneratedNotice">
      <strong>Reference table.</strong>{' '}
      Use this page for compact lookup after reading the guided docs.
    </aside>
  );
}
