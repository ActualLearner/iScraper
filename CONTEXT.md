# iScraper

iScraper is a public-beta Telegram bot that monitors Telegram channels and matches source posts to each user's saved intent. The first implementation is optimized for job and internship discovery, but the domain language stays broad enough to support other listing-like posts later.

## Language

**User**:
A person who configures iScraper and receives matched source posts.
_Avoid_: customer, intern, student

**Source Channel**:
A Telegram channel that iScraper watches for source posts.
_Avoid_: job channel, internship channel

**Source Post**:
A Telegram message from a source channel that may be matched to a user's intent.
_Avoid_: job, internship, listing

**Match Profile**:
A user's description of the kind of source posts they want iScraper to find.
_Avoid_: resume, job profile, internship profile

**Match**:
A source post that iScraper considers relevant to a user's match profile.
_Avoid_: result, hit

**Past Search**:
A one-time search over recent source posts from a user's source channels.
_Avoid_: scrape-past, backfill

**Ongoing Alert**:
A user's forward-looking alert mode for new matches from their source channels.
_Avoid_: daily alert, scrape-daily, scrape-from-now-on

**Interval Alert**:
A recurring delivery of new matches every configured number of days.
_Avoid_: scheduled alert, weekly alert, monthly alert

**Near-Live Alert**:
An ongoing alert mode that delivers each new match within a few minutes of iScraper observing the source post, by re-checking the user's source channels every configured number of minutes.
_Avoid_: live, realtime scrape, instant scrape

**Delivery Destination**:
The Telegram chat where iScraper sends matches for a user.
_Avoid_: output channel, report target

## Relationships

- A **User** may have one default **Match Profile**
- A **User** watches one or more **Source Channels**
- A **User** may configure one **Ongoing Alert**
- An **Ongoing Alert** uses one selected **Match Profile**
- An **Ongoing Alert** is off, an **Interval Alert**, or a **Near-Live Alert**
- An **Interval Alert** sends matches to one **Delivery Destination**
- A **Near-Live Alert** sends matches to one **Delivery Destination**
- A **Source Channel** contains many **Source Posts**
- A **Source Post** can become a **Match** for many **Users**
- A **Match** belongs to exactly one **User** and exactly one **Source Post**

## Example Dialogue

> **Dev:** "Should the bot only look for internships?"
> **Domain expert:** "No. Internships are the first use case, but a user's **Match Profile** can describe any kind of **Source Post** they want to monitor."

## Flagged Ambiguities

- "private" refers to the repository, not the product beta; the product beta is public.
- "job", "internship", and "listing" are common examples, but the canonical term is **Source Post**.
- Bot copy may use job/internship language for clarity, but core model, database, and service names should use the generic domain language above.
- "scrape" describes an internal implementation action; user-facing flows should use **Past Search** and **Ongoing Alert**.
- "daily alert" is too narrow for the forward-looking feature; the canonical term is **Ongoing Alert** because users choose off, every N days (**Interval Alert**), or every N minutes (**Near-Live Alert**).
- "live" implies true real-time; v1 delivers **Near-Live Alerts** by polling source channels every N minutes (driven by a scheduled job), so the canonical term is **Near-Live Alert**.
