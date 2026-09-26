import { useRuntime } from '../../runtime';
import ConversationLibrary from './ConversationLibrary';
import SearchConversations from './SearchConversations';

/**
 * Every conversation in one place: full-text search with message snippets,
 * a type filter, and bulk actions. The sidebar and ⌘K cover quick switching.
 */
export default function LibraryPage() {
  const { controller } = useRuntime();
  return (
    <div className="library-page">
      <header className="library-header">
        <h1>Conversation library</h1>
        <p className="muted">
          Search every message, filter by type, and manage conversations in
          bulk.
        </p>
      </header>
      <section className="library-search" aria-label="Search history">
        <SearchConversations />
      </section>
      <ConversationLibrary controller={controller} linkRows />
    </div>
  );
}
