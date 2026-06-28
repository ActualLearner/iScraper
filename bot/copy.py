"""User-facing copy. Job/internship-flavored per product-spec.md, while the data
model stays generic. Centralized so wording is easy to tweak."""
from __future__ import annotations

from core import config

BETA_FULL = (
    "iScraper is in a small public beta and all slots are currently full. "
    "Please check back later — thanks for your interest!"
)

WELCOME = (
    "\U0001f44b Welcome to <b>iScraper</b>!\n\n"
    "I watch public Telegram channels and groups and send you posts that match what you're "
    "looking for — great for finding jobs and internships.\n\n"
    "Let's set you up. First, your <b>match profile</b> (optional)."
)

MATCH_PROFILE_PROMPT = (
    "Describe the kind of jobs or internships you want in a sentence or two — the "
    "roles, the level (intern / entry-level), and anything that must be included or "
    "excluded. The more specific you are, the better your matches."
)

PROFILE_TOO_LONG = (
    "That's a bit long ({words} words). Please shorten it to "
    f"{config.MAX_MATCH_PROFILE_WORDS} words or fewer and send it again."
)
PROFILE_EMPTY = "That looks empty. Please send a short description, or tap a button below."

PROFILE_SAVED = "✅ Saved your match profile."
PROFILE_NONE = "You don't have a saved match profile yet."

ONBOARDING_CHANNELS_PROMPT = (
    "Now add at least one <b>source channel or group</b> to watch.\n\n"
    "Send a public channel or group as <code>@publicusername</code> or "
    "<code>https://t.me/publicusername</code>. You can send several at once, "
    "separated by spaces."
)

ADD_CHANNELS_PROMPT = (
    "Send a public channel or group as <code>@publicusername</code> or "
    "<code>https://t.me/publicusername</code>. You can send several at once, "
    "separated by spaces."
)

PRIVATE_LINK_REJECT = (
    "Private invite links are not supported yet. For now, please add a public "
    "channel or group using <code>@publicusername</code> or "
    "<code>https://t.me/publicusername</code>."
)
INVALID_FORMAT_REJECT = (
    "Please send a public Telegram channel or group as <code>@publicusername</code> or "
    "<code>https://t.me/publicusername</code>."
)

ONBOARDING_DONE = (
    "\U0001f389 You're all set! Use /settings to manage everything, /search_past "
    "to search recent posts, and /alerts to set up ongoing alerts."
)

NEED_CHANNEL_BEFORE_DONE = "You need at least one source channel before finishing."
NEED_PROFILE_FOR_RUN = (
    "Your saved match profile is empty. Choose <b>Enter a new one</b>, or save a "
    "profile from /settings first."
)
CANNOT_REMOVE_LAST = (
    "That's your only source channel — add another before removing this one."
)

MAIN_MENU = (
    "⚙️ <b>iScraper settings</b>\n\n"
    "Configure your profile, channels, delivery, and timezone here. "
    "Use the app drawer for Past Search, Search Status, and Alerts."
)

PROFILE_CHOICE = "Use your saved match profile, or enter a new one for this run?"

NEAR_LIVE_EXPLAINER = (
    "Near-Live checks your channels every few minutes and sends each new matching "
    "post as soon as it is found."
)

PAST_SEARCH_QUEUED = (
    "\U0001f50d Queued Past Search #{job_id}: last {days} day(s) across your {n} channel(s). "
    "Use /search_status to check progress."
)
NO_MATCHES = "No matches found."

CANCELLED = "Okay, cancelled. Use /settings anytime."
