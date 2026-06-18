import React, {useEffect, useMemo, useState} from 'react';
import useBaseUrl from '@docusaurus/useBaseUrl';

type PagefindResult = {
  id: string;
  data: () => Promise<{
    url: string;
    meta?: {title?: string; description?: string};
    excerpt?: string;
  }>;
};

type SearchResult = {
  id: string;
  url: string;
  title: string;
  description: string;
  excerpt: string;
};

declare global {
  interface Window {
    pagefind?: {
      search: (query: string) => Promise<{results: PagefindResult[]}>;
      options?: (options: Record<string, unknown>) => Promise<void>;
    };
  }
}

export default function PagefindSearch(): JSX.Element {
  const pagefindUrl = useBaseUrl('/pagefind/pagefind.js');
  const [query, setQuery] = useState('');
  const [ready, setReady] = useState(false);
  const [searched, setSearched] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    if (window.pagefind) {
      setReady(true);
      return;
    }
    const script = document.createElement('script');
    script.src = pagefindUrl;
    script.async = true;
    script.onload = () => setReady(Boolean(window.pagefind));
    script.onerror = () => setMessage('Search index is available after building the docs site.');
    document.body.appendChild(script);
    return () => {
      script.remove();
    };
  }, [pagefindUrl]);

  const status = useMemo(() => {
    if (message) {
      return message;
    }
    if (!ready) {
      return 'Loading search index...';
    }
    if (searched && results.length === 0) {
      return 'No results found.';
    }
    return '';
  }, [message, ready, searched, results.length]);

  async function runSearch(event?: React.FormEvent) {
    event?.preventDefault();
    const trimmed = query.trim();
    setSearched(true);
    if (!trimmed || !window.pagefind) {
      setResults([]);
      return;
    }
    const response = await window.pagefind.search(trimmed);
    const next = await Promise.all(
      response.results.slice(0, 8).map(async (result) => {
        const data = await result.data();
        return {
          id: result.id,
          url: data.url,
          title: data.meta?.title || data.url,
          description: data.meta?.description || '',
          excerpt: data.excerpt || '',
        };
      }),
    );
    setResults(next);
  }

  return (
    <section className="rowBotSearch" aria-label="Docs search">
      <form className="rowBotSearchForm" onSubmit={runSearch}>
        <input
          aria-label="Search docs"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search Row-Bot docs"
        />
        <button type="submit" disabled={!ready}>
          Search
        </button>
      </form>
      {status ? <p className="rowBotSearchStatus">{status}</p> : null}
      {results.length ? (
        <div className="rowBotSearchResults">
          {results.map((result) => (
            <a className="rowBotSearchResult" href={result.url} key={result.id}>
              <strong>{result.title}</strong>
              {result.description ? <span>{result.description}</span> : null}
              {result.excerpt ? <small dangerouslySetInnerHTML={{__html: result.excerpt}} /> : null}
            </a>
          ))}
        </div>
      ) : null}
    </section>
  );
}
