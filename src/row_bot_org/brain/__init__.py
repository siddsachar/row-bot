"""The company brain v1 (B1a, migration 0008): spaces with group access, company documents and their processing,
facts, hybrid search and recall with citations, on CON-1's contract (``row_bot.brain``).

- ``parse``: the file types the server reads, and chunking;
- ``store``: the SQL, under row-level security;
- ``service``: the worker (parse, chunk, embed through the gateway in process) and recall;
- ``web``: the routes.
"""
