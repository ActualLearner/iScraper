# iScraper Architecture Notes

## Deployment Topology

iScraper v1 runs entirely on free, no-credit-card services, split across three surfaces that share one Supabase Postgres database (with the `pgvector` extension) as the single source of truth:

- **Interactive bot** — a Telegram webhook handled by a Vercel Python serverless function. It runs only when a user sends a message or taps a button, loads and saves all conversation state in Supabase, and never holds long-running connections. It uses the Telegram **Bot API** only.
- **Scheduled worker** — a GitHub Actions workflow that runs every few minutes. It does the heavy, long-running work: scraping source channels with a Telegram **user account** (Telethon string session), embedding posts, matching, and delivering alerts. Anything that needs Telethon or can take longer than a webhook should run here.
- **Database** — Supabase Postgres + `pgvector` stores users, source channels, source posts, embeddings, matches, conversation state, and a small job queue.

Because GitHub Actions cannot host an always-on listener and its schedule granularity is ~5 minutes (and can be delayed under load), **Near-Live Alerts are implemented by polling** the worker on a short interval rather than by a persistent real-time listener. User-triggered Past Searches are enqueued as jobs by the bot and executed by the next worker run.

## Matching Flow

iScraper stores source posts globally and user preferences separately. A source post scraped for one user can be reused for every other user watching the same source channel.

## Source Post Data

Each source post should store the Telegram metadata needed for matching, deduplication, and user delivery. Telegram albums are grouped into one logical row so the caption and OCR text from the album images travel together under one canonical message link.

Minimum fields:

- Source channel identifier
- Telegram message ID
- Telegram message link
- Album grouped ID, when Telegram provides one
- Posted date
- Edited date, when Telegram provides one
- Scraped date
- Caption text
- OCR image text
- Message content
- Message content embedding

The message content is the combined OCR text plus caption text that gets embedded. The message link, posted date, channel, and message ID are metadata used to identify and return the source post.

Use direct comparison of normalized message content to detect whether a previously stored message changed. Telegram edit metadata is useful when available, but the normalized content comparison is the final check for whether stored content and embeddings need to be refreshed. OCR is only run for new or changed image posts; unchanged stored posts are skipped cheaply.

## Source Channel Validation

Before saving a source channel for a user, iScraper should normalize the accepted input format and confirm the scraper account can access the public channel. Bulk channel add should validate each submitted channel independently so valid channels can still be saved when some inputs fail.

Adding a source channel only updates the user's saved source channel list. It should not trigger historical scraping, Past Search, or Ongoing Alerts by itself.

### Near-Live Alerts

For users whose ongoing alert mode is `Every N minutes`, the scheduled worker checks whether each such user is due (now minus their last near-live check is at least their configured interval). For every due user, each near-live run:

1. Scrapes recent posts from the user's source channels (a small forward-looking window since the last check is enough).
2. Normalizes and stores any source post that has not already been stored, merging caption text with OCR text from images when present, and creates its embedding.
3. Compares the candidate source posts to the user's selected alert match profile vector.
4. Records matches that pass the user's threshold, skipping any source post already delivered to that user as a near-live match (deduplication).
5. Delivers each new match immediately as a single-link message to the user's delivery destination.
6. Updates the user's last near-live check time.

Near-Live Alerts start from the moment they are enabled and never send older posts: enabling the mode records a start time, and the worker only considers source posts observed at or after that start time. Because delivery is driven by a scheduled poll (not a persistent listener), matches arrive within roughly the configured interval rather than instantly. Near-Live Alerts do not send "No matches found" messages.

### Interval Alerts

For users whose ongoing alert mode is `Every N days`, each interval run:

1. Reads the user's last interval-alert sent time.
2. Searches stored source posts newer than that time using the user's selected alert match profile.
3. Sends matching Telegram message links to the configured delivery destination.
4. Sends a short "No matches found" message if there were no matches.
5. Updates the user's last interval-alert sent time after the run finishes.

Interval Alerts do not need to rescan all history each time. They only consider posts newer than the user's last interval-alert sent time.

### Past Search

Past Search is a user-triggered lookup over stored or newly scraped source posts within the requested lookback period. It uses the match profile selected for that run and does not update the interval-alert sent time.

When Past Search runs:

1. For each saved source channel, iterate messages from newest to oldest until the requested lookback boundary is reached.
2. Group Telegram albums into one logical source post when messages share a grouped ID.
3. Skip source posts that have no text, caption, or OCR-extractable image text.
4. If a source post is not stored, OCR/merge its text, normalize, embed, and store it.
5. If a source post is already stored, compare the current normalized content to the stored normalized content.
6. If the content differs, update the stored content, edited date, OCR text, and embedding.
7. If the content matches, skip re-embedding that source post.
8. After the channel window is up to date, run semantic search over stored source posts in the requested lookback period using the match profile selected for that run.

Past Search should not rely only on "first already scraped message" as a stop condition if edited messages inside the requested window must be detected. The safe v1 behavior is to walk the requested date window and skip unchanged stored messages cheaply by comparing normalized content directly.
