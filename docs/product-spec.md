# iScraper Product Spec

## Product Scope

iScraper is a public-beta Telegram bot that helps users find relevant job and internship opportunities from public Telegram channels. The user-facing copy is job/internship-flavored in v1, while the underlying model stays generic enough to match other kinds of source posts later.

## Beta Access

- V1 is capped at 5 users.
- A user is created when a new Telegram user first messages the bot.
- If fewer than 5 users exist, the new user consumes a beta slot and can start onboarding.
- If 5 users already exist, the bot replies with a beta-full message and does not create a user.
- There is no separate auth, invite, allowlist, or automatic expiration for v1 beta access.

## Match Profile

The match profile is the user's short description of what kinds of opportunities they want iScraper to find.

- Saving a match profile is optional during onboarding.
- Any search or alert mode requires a non-empty match profile.
- iScraper has one default saved match profile per user.
- Past Search and Ongoing Alerts both offer the same match profile choice: use the saved match profile or enter a new one.
- If the user chooses the saved match profile and it is empty, the bot should show a useful error and return to the same two choices.
- If the user chooses to enter a new match profile and submits an empty message, the bot should show a useful error and return to the same two choices.
- A new match profile entered during Past Search is used only for that Past Search run unless the user explicitly saves it from settings.
- A new match profile entered while enabling Ongoing Alerts is scoped only to that active Ongoing Alert setup, whether the chosen mode is `Every N minutes` or `Every N days`.
- A manual Ongoing Alert match profile does not replace the user's default saved match profile.
- Ongoing Alerts keep using their selected match profile until the user changes alert settings, changes alert mode, or turns alerts off.
- Limit: 35 words by default, backend-configurable.
- The bot should ask for a well-worded description, not a spammy keyword list.
- The prompt should encourage role, seniority, location, remote preference, tech stack, industry, and constraints when relevant.
- The bot should reject profiles over the configured word limit and ask the user to shorten them.

Example prompt:

> Describe the jobs or internships you want in 35 words or fewer. Keep it short and literal: name the exact role family first, then add any important include/exclude constraints.

## Source Channels

V1 supports public Telegram channels only.

- Limit: 30 source channels per user.
- Each user has one saved source channel list.
- Past Search and Ongoing Alerts both use the same saved source channel list.
- Source channel presets and per-search channel subsets are out of scope for v1.
- Onboarding must collect at least one valid source channel.
- Users cannot remove a source channel if it is the only remaining source channel in their list.
- The source channel settings view should support viewing channels, adding channels, and removing channels.
- After adding a channel from onboarding or settings, the bot should offer "Add another" and "Done".
- Users may add multiple source channels in one message by separating accepted channel inputs with whitespace.
- When adding multiple source channels, the bot should validate each channel independently, save the valid channels, and report any invalid or inaccessible channels back to the user.
- The 30-channel limit still applies to bulk channel add.
- For bulk channel add, save as many valid new channels as possible up to the 30-channel limit and report the rest as not added.
- Duplicate channels from the same message or channels already saved by the user should be reported as "already added", not as errors.
- Adding source channels should not automatically run a Past Search or enable Ongoing Alerts.

Accepted user input formats:

- `@channelusername`
- `https://t.me/channelusername`

Rejected input:

- Private invite links such as `https://t.me/+...`
- Bare usernames without `@`
- Non-Telegram URLs
- Telegram groups, private channels, or anything the scraper account cannot access

The bot should validate a source channel before saving it. Validation should confirm the input format is accepted and the scraper account can access the public channel.

Private-link rejection message:

> Private channel links are not supported yet. For now, please add a public channel using `@channelusername` or `https://t.me/channelusername`.

Invalid-format rejection message:

> Please send a public Telegram channel as `@channelusername` or `https://t.me/channelusername`.

## Search Modes

iScraper supports two main user-facing search modes in v1: Past Search and Ongoing Alerts.

### Past Search

Past Search is a one-time search across historical posts from the user's source channels.

- Suggested command: `/search_past`
- The user chooses a lookback period in days only.
- Default lookback: 15 days.
- Maximum lookback: 90 days.
- The maximum lookback should be stored as an easily changed backend configuration value.
- If the user enters a lookback longer than the configured maximum, the bot rejects it and asks for a shorter period.
- Results are delivered as a list of Telegram message links.
- Past Search uses the user's saved source channel list.
- Past Search prompts the user to use the saved match profile or enter a new match profile for that run.
- The user may save a default Past Search lookback in `/settings`.
- When the user saves a new default Past Search lookback, the bot should apply it immediately by running a Past Search with that lookback.

### Ongoing Alerts

Ongoing Alerts are forward-looking matches from the user's source channels.

- Suggested command: `/alerts`
- Default alert mode: Off.
- The user chooses one alert mode:
  - Off
  - Every N days
  - Every N minutes (Near-Live)
- `Every N days` accepts day input only.
- `Every N days` must be between 1 and 30 days.
- For `Every N days`, the user chooses one delivery time.
- Delivery time uses the user's timezone.
- Default timezone: `Africa/Addis_Ababa`.
- Timezone is not part of onboarding; users may change it later from `/settings`.
- `Every N minutes` accepts minute input only.
- `Every N minutes` must be between 5 and 1440 minutes. The minimum is 5 because the scheduled worker runs on a ~5-minute cadence; smaller intervals cannot be honored.
- The minimum near-live interval should be stored as an easily changed backend configuration value.
- `Every N minutes` delivers each matching source post within roughly its configured interval after iScraper finds it (it is near-live, not instant, because delivery is driven by a scheduled poll).
- `Every N minutes` should show explanatory copy before the user enables it: "Near-Live checks your channels every few minutes and sends each new matching post as soon as it is found."
- `Every N minutes` starts from the current moment after it is enabled; it does not send historical matches.
- Ongoing Alerts prompt the user to use the saved match profile or enter a new match profile before alerts can be enabled.
- The user chooses a delivery destination: direct message or a Telegram group where the bot has been added.
- Default delivery destination: direct message.
- V1 delivery destinations are direct message and Telegram group only; Telegram channel delivery is out of scope for v1.
- `Every N days` sends one combined message per run with a list of Telegram message links.
- `Every N minutes` sends one message per match with the Telegram message link.
- Ongoing Alerts use the selected alert match profile and the user's saved source channel list.
- `Every N days` searches from that user's last interval-alert sent time.
- If an `Every N days` alert run has no matches, the bot should still send a short "No matches found" message so the user knows the schedule is working.
- `Every N minutes` does not send "No matches found" messages.
- `Every N minutes` does not use the interval-alert sent cursor; it tracks its own start time and last-check time, and skips any source post already delivered to that user as a near-live match.

## Delivery Format

Matches are sent as Telegram message links with minimal surrounding text.

For Past Search and `Every N days` Ongoing Alerts, matches are batched into one message.

The bot should return all matches that pass the configured semantic similarity threshold. V1 does not impose a fixed "top N" result cap. If the result list is too long for one Telegram message, split it across multiple messages.

The semantic similarity threshold should be backend-configurable because useful score ranges depend on the chosen embedding model.

Example:

```text
Found 4 matches:

- https://t.me/ethiotechcareers/887
- https://t.me/addisdevs/1423
- https://t.me/remotejobsafrica/234
- https://t.me/a2sv_community/1205
```

The bot may use short labels as clickable links if they are easy to generate from the source post, but v1 should not include long summaries or copied post text.

Example:

```text
Found 3 matches:

- Backend internship - https://t.me/ethiotechcareers/887
- Junior Python role - https://t.me/addisdevs/1423
- Remote developer internship - https://t.me/remotejobsafrica/234
```

For `Every N minutes` (Near-Live) Ongoing Alerts, each match is sent as a single-link message as soon as the worker finds it.

Source posts with no text, caption, or OCR-extractable image text are skipped in v1.

## Settings

Users should be able to manage all v1 configuration from `/settings`.

Settings should include:

- Match profile
- Source channels
- Past Search
- Ongoing Alerts
- Delivery destination
- Timezone

Commands such as `/search_past` and `/alerts` may exist as shortcuts, but `/settings` is the central place to configure everything.

## Later Versions

Possible post-v1 features:

- Source channel presets
- Per-search source channel subsets
- Telegram channel delivery
- Private source channels and invite links
