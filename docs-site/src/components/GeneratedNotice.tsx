import React from 'react';

type GeneratedNoticeProps = {
  source?: string;
};

export default function GeneratedNotice({source}: GeneratedNoticeProps): JSX.Element {
  return (
    <aside className="rowBotGeneratedNotice">
      <strong>Generated reference.</strong>{' '}
      This page is refreshed from Row-Bot source metadata during the docs build.
      {source ? <> Source: <code>{source}</code>.</> : null}
    </aside>
  );
}
