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
- Three composite tools: x_read (search/read), x_post (post/reply/quote/delete),
  x_engage (like/unlike/repost/bookmark).

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
- All post operations (post, reply, quote, delete) require approval.
- Tweets have a 280-character limit for text content.
- When replying, always include the tweet_id of the tweet being replied to.
- When quoting, include both text (your commentary) and tweet_id (the tweet being quoted).

ENGAGEMENT PATTERNS:
- Like, repost, and bookmark operations are reversible (unlike, unrepost, unbookmark).
- Always confirm with the user before engaging with tweets on their behalf.

BACKGROUND TASK EXAMPLES:
- For monitoring tasks, use search with start_time='1h' to check recent tweets
  since the last poll. Use persistent_thread to track what was already seen.
- For engagement tasks, combine x_read search with x_engage actions.
