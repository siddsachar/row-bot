import React from 'react';
import Layout from '@theme/Layout';
import PagefindSearch from '@site/src/components/PagefindSearch';

export default function SearchPage(): JSX.Element {
  return (
    <Layout title="Search" description="Search Row-Bot public documentation.">
      <main className="container margin-vert--lg">
        <h1>Search</h1>
        <PagefindSearch />
      </main>
    </Layout>
  );
}
