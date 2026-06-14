# iScraper

A public-beta Telegram bot that watches public Telegram channels and sends each
user the posts that match their saved intent (great for finding jobs and
internships). See [`CONTEXT.md`](CONTEXT.md) for the domain language and
[`docs/`](docs/) for the product spec and architecture.

Everything below runs on **free tiers with no credit card required**.

---

## How it works (3 free surfaces, 1 database)

```
        Telegram                 Telegram (public channels)
           │                              ▲
   webhook │ updates                      │ MTProto (user account)
           ▼                              │
   ┌─────────────────┐            ┌───────────────────────┐
   │  Vercel  (free) │            │  GitHub Actions (free) │
   │  Python webhook │            │  worker, every ~5 min  │
   │  interactive bot│            │  scrape · OCR · embed │
   │                 │            │  match · deliver       │
   └────────┬────────┘            └───────────┬───────────┘
            │      writes/reads               │
            └──────────────┬──────────────────┘
                           ▼
                 ┌──────────────────────┐
                 │  Supabase (free)     │
                 │  Postgres + pgvector │
                 └──────────────────────┘
                           ▲
                           │ embeddings
                 ┌──────────────────────┐
                 │  Google Gemini (free)│
                 └──────────────────────┘
```

- **Interactive bot** — a Telegram **webhook** handled by a Vercel Python
  serverless function. Responds instantly to `/start`, `/settings`,
  `/search_past`, `/alerts`, and button taps. Stateless: all conversation state
  lives in Supabase. Uses the **Bot API** only.
- **Worker** — a **GitHub Actions** workflow that runs every ~5 minutes. Scrapes
  channels with a Telegram **user account** (Telethon), OCRs image-based posts offline with Tesseract, embeds posts with Gemini,
  matches them, and delivers Interval / Near-Live alerts and queued Past Searches.
- **Database** — Supabase Postgres + `pgvector`, the single source of truth and a
  small job queue.

Because GitHub Actions has no always-on process, **"Live" is implemented as
Near-Live**: the worker polls every N minutes (minimum 5). Matches arrive within
a few minutes, not instantly.

---

## What you'll need (all free, no card)

| Service | Used for | Where |
|---|---|---|
| Telegram **bot** | the bot users talk to | [@BotFather](https://t.me/BotFather) |
| Telegram **API credentials** | the scraper user account | <https://my.telegram.org> |
| A Telegram **user account** | reads public channels | a phone number you control |
| **Supabase** | database | <https://supabase.com> |
| **Google AI Studio** | Gemini embeddings | <https://aistudio.google.com/app/apikey> |
| **Vercel** | webhook hosting | <https://vercel.com> |
| **GitHub** | the worker (Actions) | <https://github.com> |

> The scraper logs in as a **real Telegram user account** (not the bot) because
> bots can't read arbitrary public channel history. Use an account you control;
> a secondary number is fine. Reading public channels is normal usage, but the
> session string grants full access to that account — keep it secret.

---

## Setup, in order

### 1. Put the code on GitHub
Create a repo and push this project. **Make it public** — see the cost note
below; public repos get unlimited Actions minutes, which this app needs. No
secrets live in the code (they go in Actions secrets / Vercel env vars, and
`.env` is git-ignored).

### 2. Supabase (database)
1. Create a free project.
2. Open **SQL Editor → New query**, paste all of
   [`scripts/init_db.sql`](scripts/init_db.sql), and run it.
3. From **Project Settings → API**, copy the **Project URL** (`SUPABASE_URL`) and
   the **`service_role` key** (`SUPABASE_SERVICE_KEY`). The service-role key is
   server-side only — never expose it client-side.

### 3. Gemini API key
Create a key at <https://aistudio.google.com/app/apikey> → `GEMINI_API_KEY`.

### 4. Create the bot
1. Talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
   (`BOT_TOKEN`).
2. (Optional, for group delivery) `/setprivacy` → select your bot → **Disable**,
   so the bot can see the `/here` command inside groups. (If you keep privacy on,
   users can still register a group by sending `/here@yourbotname`.)

### 5. Generate the scraper session (local, one time)
On your own machine (you need to receive the Telegram login code):
```bash
cp .env.example .env          # fill in TELEGRAM_API_ID, TELEGRAM_API_HASH
pip install -r requirements-worker.txt
python scripts/login_telethon.py
```
It logs in interactively and prints a `TELETHON_SESSION` string. Copy it.

### 6. Deploy the webhook to Vercel
1. <https://vercel.com> → **Add New → Project** → import your GitHub repo.
   Framework preset: **Other**. No build command needed; it auto-detects the
   Python function in `api/`.
2. Add **Environment Variables** (Project → Settings → Environment Variables) —
   the webhook only needs these four:
   - `BOT_TOKEN`
   - `WEBHOOK_SECRET` (any long random string you choose)
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
3. **Deploy**, then copy your production URL, e.g. `https://iscraper.vercel.app`.

### 7. Register the webhook
Locally (with `.env` containing `BOT_TOKEN` and the same `WEBHOOK_SECRET`):
```bash
python scripts/set_webhook.py https://iscraper.vercel.app
```
Message your bot `/start` — onboarding should respond immediately.

### 8. Turn on the worker (GitHub Actions)
1. In your repo: **Settings → Secrets and variables → Actions → New repository
   secret**, add all of:
   - `BOT_TOKEN`
   - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELETHON_SESSION`
   - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`
   - `GEMINI_API_KEY`
2. Open the **Actions** tab and enable workflows if prompted.
3. The **iScraper worker** runs every ~5 minutes. Use **Run workflow**
   (workflow_dispatch) to trigger it immediately the first time.

The workflow installs Tesseract automatically so poster images can be OCRed before embedding.

You're live. 🎉

---

## Local development / testing

```bash
pip install -r requirements-worker.txt
cp .env.example .env            # fill everything in

# run one worker pass against your real Supabase/Telegram/Gemini:
# install tesseract-ocr locally first if you want OCR in local runs
python -m scraper.runner
```

For the bot locally you can either keep using the Vercel webhook, or temporarily
`python scripts/set_webhook.py --delete` and add a polling loop (not included in
v1, which is webhook-first).

---

## Honest free-tier caveats (please read)

- **Near-live is not instant.** GitHub Actions cron has 5-minute granularity and
  scheduled runs are *best-effort* — they're often delayed, especially around the
  top of the hour. Expect matches "within a few minutes," and the minimum
  near-live interval is **5 minutes**. Interval-alert delivery times are honored
  within a ~20-minute window for the same reason.
- **Use a public repo for unlimited Actions minutes.** Private repos get only
  ~2,000 free minutes/month; running every 5 minutes (~288 runs/day) would burn
  that in a few days. Public repos are unlimited. (No secrets are in the code.)
- **GitHub disables scheduled workflows after 60 days of repo inactivity.** Push
  any commit, or hit **Run workflow** manually, occasionally to keep the schedule
  alive.
- **Supabase free projects pause after ~7 days of inactivity** — but the worker
  touches the DB every 5 minutes, so it stays awake on its own.
- **Gemini free tier has rate limits** (requests/minute and tokens/day). Fine for
  the 5-user beta; embeddings are batched and unchanged posts are never
  re-embedded.
- **Vercel Hobby is free with no card** and is plenty for a webhook. The function
  stays small because heavy deps (Telethon, Gemini) live only in the worker.

**Total cost: $0, no credit card.**

---

## Tunables

Defaults live in [`core/config.py`](core/config.py) and can be overridden with
environment variables (set them in Vercel and/or as Actions secrets/vars):

| Var | Default | Meaning |
|---|---|---|
| `SIMILARITY_THRESHOLD` | `0.70` | cosine similarity cutoff for a match |
| `BETA_MAX_USERS` | `5` | beta slot cap |
| `MAX_MATCH_PROFILE_WORDS` | `35` | saved/new match profile word limit |
| `MAX_LOOKBACK_DAYS` | `90` | Past Search maximum lookback |
| `DEFAULT_LOOKBACK_DAYS` | `15` | Past Search default lookback |
| `MIN_NEAR_LIVE_MINUTES` | `5` | smallest near-live interval |
| `DEFAULT_TIMEZONE` | `Africa/Addis_Ababa` | default user timezone |
| `OCR_LANGS` | `eng` | Tesseract language codes for poster OCR, e.g. `eng+amh` |
| `OCR_TIMEOUT_SECONDS` | `8` | per-image OCR timeout |
| `OCR_TESSERACT_CONFIG` | `--psm 6` | extra Tesseract flags |

If you change `EMBEDDING_MODEL` / `EMBEDDING_DIM`, update the `vector(768)`
columns and the `match_source_posts` signature in `scripts/init_db.sql` to match.

---

## Project layout

```
api/webhook.py            Vercel serverless webhook (entrypoint)
bot/                      interactive bot: router, flows, copy, keyboards
core/                     shared library: config, db, embeddings, telegram, etc.
scraper/                  Telethon client, channel scraping, worker runner
jobs/                     past search, interval alerts, near-live alerts
scripts/                  init_db.sql, login_telethon.py, set_webhook.py
.github/workflows/cron.yml  the every-5-minutes worker
requirements.txt          Vercel (light) deps
requirements-worker.txt   worker + scripts (heavy) deps
```
