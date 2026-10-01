import ConversationLibrary from './ConversationLibrary';

/**
 * Every conversation in one place: one search for titles and messages, a
 * type filter, and bulk actions. The sidebar and ⌘K cover quick switching.
 */
export default function LibraryPage() {
  return (
    <div className="library-page">
      <header className="library-header">
        <h1>Conversation library</h1>
      </header>
      <ConversationLibrary />
    </div>
  );
}
