---
name: x_guide
display_name: X (Twitter) Guide
icon: "𝕏"
description: Essential guidance for using X (Twitter) tools correctly — search syntax, time filtering, and engagement patterns.
tools:
  - x
version: "1.0"
tags:
  - social
  - x
  - twitter
author: Row-Bot
---

X (TWITTER) TOOL GUIDANCE:
- You have X tools for reading, posting, and engaging on X (Twitter).
- Four tools: x_accounts (the connected accounts), x_read (search/read),
  x_post (post/reply/quote/delete), x_engage (like/unlike/repost/bookmark).

ACCOUNTS:
- The person may have several X accounts connected (say, a personal one and
  a product one). x_accounts lists each one's @handle, name and numeric ID.
- Name the account on every x_post and x_engage call: account='@handle'.
  With several connected, a call without one is refused. Never guess: if the
  person hasn't said which account, ask them, offering the handles.
- There is no "current account"; each call says which one it acts as.

SEARCH QUERY RULES (x_read with action='search'):
- The query parameter accepts PLAIN KEYWORDS and official X API v2 operators ONLY.
- SUPPORTED operators: from:username, to:username, @username, #hashtag,
  is:retweet, is:reply, is:quote, has:links, has:media, has:images,
  lang:en, -keyword (exclude), "exact phrase" (quoted).
- UNSUPPORTED operators (will cause API errors): since:, until:,
  within_time:, near:, geocode:. These are Twitter web UI operators
  and do NOT work with the API.
- For TIME FILTERING, use the start_time and end_time parameters instead.
  These accept ISO 8601 (e.g. '2026-04-14T00:00:00Z') or relative
  formats like '1h', '24h', '7d', '2w'.
- The search endpoint only covers the last 7 days of tweets.

POSTING RULES:
- Every post, reply, quote or delete asks the person on a card naming the
  account, with the exact text and media. Only the person approves it.
- The result gives the post's link: share it with the person.
- If a result says "Not sure it went through", Row-Bot has already checked the
  account's latest posts. Report what it says; never send the post again.
- How long a post may be, and its media, depend on the account: x_accounts says (25,000 characters for
  an account with long posts, else 280). x_post checks the length before the card.
- A post for later: x_post with post_at ('13:00' or '2026-10-29T09:30'). Its card waits for the person
  in Home › Review, where they edit, approve or say no; approved, it goes out at that time. Make a run's
  posts in one step (several x_post calls at once) so they wait there together.
- When replying, always include the tweet_id of the tweet being replied to.
- When quoting, include both text (your commentary) and tweet_id (the tweet being quoted).

ENGAGEMENT PATTERNS:
- Like, repost, and bookmark operations are reversible (unlike, unrepost, unbookmark).
- Always confirm with the user before engaging with tweets on their behalf.

BACKGROUND TASK EXAMPLES:
- For monitoring tasks, search with start_time since the last check and keep the newest id you saw in the
  workflow's notes, so the next run starts from there.
- For engagement tasks, combine x_read search with x_engage actions.
