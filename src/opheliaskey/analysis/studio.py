"""Floating production studio.

The boat becomes a stage: songwriters perform aboard, the set is livestreamed,
the audio runs through an Audient iD4 mkII, and Lyric Show — the owner's
caption app — composites live lyrics onto the stream through its OBS browser
source. The stream is the marketing: viewers watch the overlay work and
install the app. The buyer is the traveler — anyone across a language line
who sees two-language captions on screen and wants Conversation Mode on a
phone of their own. Performers, streamers and worship teams are the second
segment, the original funnel, and the model carries the two by need — each
with its own conversion rates, churn and plan — kept apart all the way to the
steady state and summed only there.

The honest premise is that **almost none of the return is known yet.** What the
electrical spec can carry is arithmetic; what Starlink can lift is a budget with
a stated margin; what the kit costs is a price list. What a show sends back in
subscriptions is a *model* — a chain of conversion rates declared in one table,
every one of them arguable — and the moment shows are actually logged, the
observed viewers and install rate replace the assumed ones. Every funnel input
says which it is.

Four questions, answered separately:

  1. **Power** — can the bank run a set, and how many of them?
  2. **Uplink** — does the Starlink upload clear the encoder bitrate with margin?
  3. **Kit** — what does the studio cost beyond what is already aboard?
  4. **Return** — what the show plausibly sends back, in three lenses:
     subscriptions, paid installs displaced, and a catalog deliberately left
     unpriced.

The three return lenses measure different things and are never summed. A
"total return" would add a recurring revenue stream to a cost avoided to a
thing that is not priced at all — three incompatible units in one number.

**Competition nights.** The boat also hosts Paradise Busker song competitions:
acts rotate across the cockpit deck, the swim platform and the rear dock, the
crowd stands on the pier, and the same Lyric Show overlay that captions the
stream captions the dock screen. That makes two audiences — viewers online
and attendees in person — and they are modeled as two audiences, each with
its own conversion rate, kept apart all the way to installs and only then
summed. What the competition itself earns (BNDS tips, votes, Treasures,
memberships) is Paradise Busker's economy and the busker's, not the studio's;
the flow and the facts are reported so the reader can see where Lyric Show
is on screen, and nothing from them is added to the return.

**Baseline and ROI.** Real traction — today's paying subscribers — lives in
App Store Connect, Google Play and the Firestore entitlements, not on this
machine, so the model takes a declared `baseline_subscribers` the owner can
set, and says plainly that 0 means "not entered", not zero. The trajectory
starts at the baseline, and every row also carries the show-driven figures —
the subscribers the shows bring, with the decaying baseline subtracted —
because payback and ROI are judged on those alone: the studio cannot claim
return from subscribers it did not bring.

**What a show can and cannot observe.** A logged solo set measures viewers
per set and, when both counts were written down, the stream install rate; a
logged competition night measures the dock crowd and how much bigger its
online audience was than a set's. Nothing measures the attendee install rate,
because nobody on the dock is counting who installed. An observation that
fails the range an override is held to — a negative count, a rate above 1 —
is reported and ignored, never modeled.

**Reach and the target.** The boat is not the only stage the overlay is on.
Partner channels — a church that already has the OBS overlay and will caption
its Sunday streams, an artist with millions of subscribers who might stream a
live set through it — put the same captions in front of audiences the boat
could never draw, and because the captions render in each viewer's own
language, one stream crosses every language border its audience does. The
partners are data: each row says whether it has committed (counted by
default) or is hypothetical (counted only when the switch is on), where its
audience figure comes from, and how much of that audience is abroad — a
declared share, not a measurement. The audience figure that is not on this
machine is a labelled placeholder until the owner enters it, and the report
says so on the row. Partner viewers join the stream audience before the
traveler/performer split, so they install and pay at the same declared rates
as everyone else. The owner's target — so many paying subscribers by such a
month — is read against the trajectory and answered in its own terms: where
the book stands that month, when (if ever) the target is reached within the
horizon, and what it would take at the current rates and partner set.
"""

from __future__ import annotations

import copy
import math

from ..classify.taxonomy import BOAT_SYSTEMS, VESSEL_META
from ..db.database import Database
from .cost import totals
from .spec import ASSUMPTIONS, load_spec

# --- facts ------------------------------------------------------------------
# Plan prices and overlay capabilities are read from the app's own source, not
# estimated. A price change belongs here and nowhere else.

LYRICSHOW: dict = {
    "source": "lyric-show web/src/lib/tiers.ts (2026-06-23); "
              "ios/LyricShow/LyricShow.storekit (2026-07-06)",
    "plans": {
        "free": {
            "name": "Free", "kind": "tier", "monthly_cents": 0, "annual_cents": None,
            "note": "Typewriter mode only, English and Spanish"},
        "base": {
            "name": "Base", "kind": "tier", "monthly_cents": 1499, "annual_cents": 9999,
            "note": "The paid tier; add-ons stack on it"},
        "ultimate": {
            "name": "Ultimate", "kind": "tier", "monthly_cents": 4999, "annual_cents": 39999,
            "note": "Includes Pro Broadcast and Extended Languages"},
        "pro_broadcast": {
            "name": "Pro Broadcast", "kind": "add-on", "monthly_cents": 3999, "annual_cents": None,
            "note": "OBS overlay and broadcast/video-call captions; requires Base, "
                    "included in Ultimate"},
        "extended_languages": {
            "name": "Extended Languages", "kind": "add-on", "monthly_cents": 999,
            "annual_cents": None, "note": "Included in Ultimate"},
        "conversation_mode": {
            "name": "Conversation Mode", "kind": "add-on", "monthly_cents": 499,
            "annual_cents": 3999,
            "note": "Two speakers, two languages, one screen; requires Base or Ultimate"},
    },
    "caption_latency_ms": 300,
    "languages": {"base": 20, "total": 80},
    "fx_modes": 6,
    "platforms": ["iOS", "web PWA", "Android TWA"],
    "overlay": {
        "browser_source": "/obs/ (token-secured)",
        "relay": "Firebase RTDB",
        "encoders": ["OBS Studio", "Streamlabs", "XSplit", "vMix", "Wirecast", "Ecamm",
                     "Restream"],
        "destinations": ["Twitch", "YouTube", "Facebook", "Kick", "Rumble", "LinkedIn",
                         "TikTok", "custom RTMP"],
    },
    "audio_input": "The web PWA runs on-device speech recognition (Vosk WASM) from any "
                   "CoreAudio input, so one iD4 feeds OBS and Lyric Show on the same Mac",
}

_PLANS = LYRICSHOW["plans"]
# The add-on travelers subscribe for, and the featured bundle it sells in. The
# bundle is Base plus the add-on on each billing term, so a price change in
# `plans` moves it; what it does is why a stream can demo it live.
LYRICSHOW["conversation_mode"] = {
    "monthly_cents": _PLANS["conversation_mode"]["monthly_cents"],
    "annual_cents": _PLANS["conversation_mode"]["annual_cents"],
    "bundle_annual_cents": (_PLANS["base"]["annual_cents"]
                            + _PLANS["conversation_mode"]["annual_cents"]),
    "bundle_monthly_cents": (_PLANS["base"]["monthly_cents"]
                             + _PLANS["conversation_mode"]["monthly_cents"]),
    "what": "Two speakers, two languages, one screen — the top half rotated for the person "
            "opposite; automatic speaker switching; the iOS app broadcasts conversation "
            "captions to the OBS overlay, so a stream can demo it live",
    "source": "lyricshow.live/pricing 2026-08-22; tiers.ts FEATURED_PACKAGE",
}

# The plans a show-driven subscriber plausibly lands on, priced from the table
# above so a price change there moves the funnel. Two segments, two kinds of
# plan: a traveler lands on the featured bundle, Base + Conversation Mode —
# one plan, so it is its segment's whole mix — and a performer lands on one of
# three performer plans in the declared `mix_*` proportions. Each plan carries
# its list monthly price and its annual price: a share of each segment
# (`traveler_annual_share` on the traveler plan, `annual_share` on the
# performer plans) pay annually, and their month costs the annual price ÷ 12.
# Pro Broadcast has no annual price, so "Base + Pro Broadcast" annual is
# Base's annual plus twelve months of the add-on — algebraically the same as
# blending Base alone and adding the add-on at its monthly price, and it keeps
# the blend one formula for every plan, here and in the page's JS mirror.
# (key, name, monthly_cents, annual_cents, segment, mix key — None for the
# traveler plan, which is its segment's whole mix)
FUNNEL_PLANS: tuple[tuple[str, str, int, int | None, str, str | None], ...] = (
    ("traveler_bundle", "Base + Conversation Mode",
     LYRICSHOW["conversation_mode"]["bundle_monthly_cents"],
     LYRICSHOW["conversation_mode"]["bundle_annual_cents"], "traveler", None),
    ("base", "Base", _PLANS["base"]["monthly_cents"], _PLANS["base"]["annual_cents"],
     "performer", "mix_base"),
    ("base_broadcast", "Base + Pro Broadcast",
     _PLANS["base"]["monthly_cents"] + _PLANS["pro_broadcast"]["monthly_cents"],
     _PLANS["base"]["annual_cents"] + 12 * _PLANS["pro_broadcast"]["monthly_cents"],
     "performer", "mix_base_broadcast"),
    ("ultimate", "Ultimate", _PLANS["ultimate"]["monthly_cents"],
     _PLANS["ultimate"]["annual_cents"], "performer", "mix_ultimate"),
)

# --- declared assumptions ---------------------------------------------------
# Every judgement call in the module. Power draws come from device ratings, the
# uplink figures from Starlink Roam as seen in the field, and the funnel from
# consumer-app benchmarks — which is to say the funnel is the part to argue with.
# The traveler rates encode the owner's premise that 1% of viewers subscribe
# for Conversation Mode, declared here as an arguable assumption like the rest.

STUDIO_ASSUMPTIONS: dict[str, tuple[float, str]] = {
    # power (watts)
    "load_encoder_w":    (
        65, ("Studio host running the encoder and the Producer; vision tracking and "
             "per-source colour correction run continuously, above a plain encode")),
    "load_interface_w":  (
        6, ("USB bus-powered audio interfacing for the close pair and the onboard "
            "stereo pair, phantom power included")),
    "load_starlink_w":   (90, "Starlink dish and router, average; Gen 3 ranges 75–100 W"),
    "load_cameras_w":    (
        65, ("Six pan-tilt cameras on the recorder's PoE, the recorder itself, and the "
             "handheld charging between sets; the handheld is wireless, so it costs a "
             "charger rather than a capture path")),
    "load_lighting_w":   (80, "Two LED panels at show brightness"),
    "load_monitoring_w": (60, "Performer monitoring and PA at show level"),
    "show_hours":        (2.0, "A livestream set: setup, the show and the encore"),
    # uplink
    "starlink_upload_mbps_low":  (
        8.0, "Starlink Roam upload at the congested low end seen in the field"),
    "starlink_upload_mbps_high": (25.0, "Starlink Roam upload, uncongested"),
    "bitrate_1080p_mbps": (6.0, "1080p60 H.264 at a conservative streaming bitrate"),
    "bitrate_2160p_mbps": (18.0, "2160p30 H.264/HEVC"),
    "uplink_headroom":    (
        1.5, "Upload must exceed the encoder bitrate by this ratio or the encoder drops frames"),
    # funnel
    "viewers_per_show": (
        150, "Unique viewers per livestream at launch scale: one set, one platform"),
    "shows_per_month":  (4, "Weekly show cadence"),
    # the performer share — streamers, worship teams, the original funnel
    "viewer_to_install": (
        0.025, ("Performer-share stream viewers who install after seeing the overlay and the "
                "call to action; passive-viewer click-to-install runs 1–3%, so 2.5% assumes "
                "an explicit on-screen ask")),
    "install_to_paid": (
        0.05, ("Performer installs that start a paid plan within 30 days; freemium utility "
               "apps convert 2–5%, performers with a specific need sit at the top of that "
               "band")),
    "mix_base":           (0.60, "Share of new paid performers on Base alone"),
    "mix_base_broadcast": (
        0.15, ("Share of new paid performers on Base plus the Pro Broadcast add-on: the "
               "performer-streamer who saw it work")),
    "mix_ultimate":       (0.25, "Share of new paid performers on Ultimate"),
    "annual_share":       (
        0.35, ("Share of paying performers on annual billing; their price is the annual "
               "price ÷ 12")),
    "monthly_churn":      (
        0.08, "Monthly churn of the performer share; consumer subscription apps run 5–10%"),
    # the traveler share — the primary buyer; Conversation Mode is the need
    "traveler_share": (
        0.70, ("Share of any audience whose need is Conversation Mode — travelers, "
               "cross-language families and workplaces; the rest are performers, streamers "
               "and worship teams (the original funnel)")),
    "traveler_viewer_to_install": (
        0.04, ("Travelers who install after seeing two-language captions on screen; four "
               "times a passive click-through because the need is immediate")),
    "traveler_install_to_paid": (
        0.25, ("Travelers who start the Base + Conversation Mode bundle within 30 days; the "
               "two defaults multiply to the stated 1% of viewers → subscribers — the figure "
               "every traveler met so far says they'd pay")),
    "traveler_annual_share": (
        0.60, ("Travelers buying the annual bundle rather than month to month; the annual is "
               "the featured best value and trips are planned ahead")),
    "traveler_monthly_churn": (
        0.06, "Trip-driven subscribers churn between trips; annual billing holds them a year"),
    "store_commission":   (
        0.15, "Apple Small Business Program rate under $1M/yr; 30% above it"),
    "baseline_subscribers": (
        0, ("Paying subscribers today, from App Store Connect / Google Play / Firestore; 0 means "
            "not entered, not zero — the trajectory starts here")),
    "paid_cpi_dollars":   (
        3.50, ("Cost per install through paid ads for a US iOS utility app in 2026 — what "
               "each show-driven install displaces")),
    "songs_per_show":     (8, "Songs performed and captured per set"),
    "horizon_months":     (36, "Projection horizon"),
    # competition nights
    "events_per_month":   (2, "Paradise Busker competition nights hosted from the boat"),
    "buskers_per_event":  (6, "Acts per competition night across the three stages"),
    "event_viewers_multiplier": (
        2.0, "A competition night draws this multiple of a solo set's online viewers"),
    "dock_attendees_per_event": (
        60, "People on the rear dock and swim platform for a competition night"),
    "attendee_to_install": (
        0.12, ("Dock attendees who install with the QR on the overlay and the captions in "
               "front of them; in-person with a demo converts several times better than a "
               "stream")),
    # partner streams — other stages the overlay is on
    "partner_artist_subscribers": (
        6_190_000, "The partner artist's YouTube subscribers, as given on 2026-08-22 "
                   "(the partner artist's channel; not named here)"),
    "partner_church_live_viewers": (
        5_000, ("ESTIMATE: the partner church's live viewers per Sunday stream; not on this "
                "machine — replace with the channel's analytics (okey studio partner or "
                "project_meta)")),
    "partner_live_share": (
        0.02, ("Share of a channel's subscribers who watch a given live stream or its "
               "first-week replay; live concurrents run 0.5–2% of subscribers, replays "
               "several times that")),
    "partners_include_hypothetical": (
        0, ("1 counts partners that have not committed yet (Partner artist); 0 counts only "
            "committed partners (Partner church)")),
    # the owner's target
    "target_subscribers": (3_000, "Target: paying subscribers to reach — set to 3,000 by "
                                  "month 3 on 2026-08-23"),
    "target_month": (
        3, "Month by which the target is reached; the projection is 3,000 in 3 months"),
}

OVERRIDABLE: frozenset[str] = frozenset(STUDIO_ASSUMPTIONS)

# Declared defaults that are stand-ins, not estimates: a figure that exists
# somewhere the model cannot read (a partner channel's analytics) and is held
# at a labelled placeholder until the owner enters it. The report flags the
# row that depends on one for as long as the declared value is the value in
# use; a figure entered through project_meta or an override is no longer the
# placeholder. Each key's note begins with the word, so the table says so too.
PLACEHOLDERS: frozenset[str] = frozenset({"partner_church_live_viewers"})

# Validation classes. Probabilities live in [0, 1]; the two churns additionally
# must be positive because each steady state divides by its own; the performer
# plan mix must be a partition. Months are whole numbers within the horizon's
# range; a flag is exactly 0 or 1; the target is a whole number of subscribers.
# ZERO_OK are the counts that may honestly be nothing: a month with no
# competition nights, a night nobody came to the dock for, a baseline not yet
# entered, a partner whose audience is set to nothing, or no target at all, is
# a scenario, not an error. Everything else must be positive.
PROBABILITIES: frozenset[str] = frozenset({
    "viewer_to_install", "install_to_paid", "mix_base", "mix_base_broadcast",
    "mix_ultimate", "annual_share", "monthly_churn", "store_commission",
    "attendee_to_install", "traveler_share", "traveler_viewer_to_install",
    "traveler_install_to_paid", "traveler_annual_share", "traveler_monthly_churn",
    "partner_live_share",
})
CHURN_KEYS: tuple[str, ...] = ("monthly_churn", "traveler_monthly_churn")
MONTH_KEYS: tuple[str, ...] = ("horizon_months", "target_month")
WHOLE_KEYS: tuple[str, ...] = MONTH_KEYS + ("target_subscribers",)
FLAG_KEYS: frozenset[str] = frozenset({"partners_include_hypothetical"})
ZERO_OK: frozenset[str] = frozenset({
    "events_per_month", "dock_attendees_per_event", "baseline_subscribers",
    "partner_church_live_viewers", "partner_artist_subscribers", "target_subscribers",
})
MIX_KEYS: tuple[str, ...] = ("mix_base", "mix_base_broadcast", "mix_ultimate")
LOAD_KEYS: tuple[str, ...] = (
    "load_encoder_w", "load_interface_w", "load_starlink_w", "load_cameras_w",
    "load_lighting_w", "load_monitoring_w",
)
# The partner audience figures and the switch are funnel inputs — they feed
# the month's viewers — and the target keys ride with them because the page's
# mirror reads everything a control can move from one table; none of them is
# observable from a show.
FUNNEL_INPUT_KEYS: tuple[str, ...] = (
    "viewers_per_show", "shows_per_month", "viewer_to_install", "install_to_paid",
    "monthly_churn", "store_commission", "annual_share", "baseline_subscribers",
    "events_per_month", "event_viewers_multiplier", "dock_attendees_per_event",
    "attendee_to_install", "traveler_share", "traveler_viewer_to_install",
    "traveler_install_to_paid", "traveler_annual_share", "traveler_monthly_churn",
    "partner_church_live_viewers", "partner_artist_subscribers", "partner_live_share",
    "partners_include_hypothetical", "target_subscribers", "target_month",
)
# Funnel inputs that a logged show can measure, and the recorded field that
# replaces them. Viewers per show and the stream install rate come from solo
# sets only — a competition night's audience is the multiplied one, and
# feeding it into the per-set average would multiply it twice. The multiplier
# itself is observed as the ratio of the two averages, competition-night
# viewers over set viewers, so a recorded competition audience replaces
# "viewers per set × multiplier" with what the nights actually drew; with only
# one kind of show logged there is no ratio and the declared multiple stands.
# Nothing else in the table is observable from a show — the attendee install
# rate in particular, because nobody at the dock is counting who installed, and
# nothing about the traveler share: which need a viewer had, and whether they
# paid for it, is the stores' record, not the show log's.
OBSERVABLE: dict[str, str] = {
    "viewers_per_show": "observed_viewers_per_show",
    "viewer_to_install": "observed_viewer_to_install",
    "event_viewers_multiplier": "observed_event_multiplier",
    "dock_attendees_per_event": "observed_attendees_per_event",
}

# --- kit and signal chain ---------------------------------------------------

# What the studio costs beyond what is already aboard. (name, cents, note)
STUDIO_KIT: list[tuple[str, int, str]] = [
    ("Audient iD4 mkII", 19999,
     "USB-C interface: one console mic pre, JFET DI, 24-bit/96 kHz, bus-powered"),
    ("Dynamic vocal microphone", 9900,
     "Shure SM58 or equivalent; rejects the engine room and the wind"),
    ("Camera capture", 12999,
     "HDMI capture so a mirrorless or phone is the performance camera"),
    ("LED light panels (2)", 15998,
     "Bi-colour panels: the cabin is dark and the water is bright"),
    ("Stands, mounts, cabling", 8000, "Mic stand, camera mount, XLR, USB-C, HDMI"),
    ("Portable acoustic treatment", 12000,
     "Moving blankets and a reflection filter for a fibreglass cabin"),
]

# Ledger descriptions that mean a kit item has already been bought.
KIT_KEYWORDS: tuple[str, ...] = (
    "audient", "id4", "sm58", "microphone", "cam link", "capture card", "light panel",
    "xlr", "reflection filter",
)

SIGNAL_CHAIN: list[dict] = [
    {"stage": "Performer", "role": "Voice and guitar",
     "detail": "Vocal mic and instrument DI into the interface", "latency_ms": None},
    {"stage": "Audient iD4 mkII", "role": "USB-C audio interface",
     "detail": "Console mic pre and JFET DI at 24-bit/96 kHz; bus-powered from the Mac",
     "latency_ms": None},
    {"stage": "Mac · OBS Studio", "role": "Encoder",
     "detail": "Cameras and iD4 audio composited; the Lyric Show overlay is a browser "
               "source, so captions are burned in before encoding and stay in sync on "
               "every platform",
     "latency_ms": None},
    {"stage": "Lyric Show", "role": "Live captions and lyrics",
     "detail": "The web app on the same Mac reads the iD4 as its microphone; on-device "
               "speech recognition, translation, token-secured overlay",
     "latency_ms": LYRICSHOW["caption_latency_ms"]},
    {"stage": "Starlink", "role": "Uplink",
     "detail": "Roam service already aboard; the upload budget is checked below",
     "latency_ms": None},
    {"stage": "Platforms", "role": "YouTube · Twitch · Kick · Facebook",
     "detail": "One RTMP stream to the platform of the night", "latency_ms": None},
    {"stage": "Viewers", "role": "Audience",
     "detail": "Watch the set, see the overlay working, hear the call to action",
     "latency_ms": None},
    {"stage": "App Store · Google Play", "role": "Installs",
     "detail": "Free tier first; Base + Conversation Mode for the traveler, Base, Ultimate "
               "and Pro Broadcast for the performer, behind the paygate",
     "latency_ms": None},
]

# --- competition nights -----------------------------------------------------
# Paradise Busker song competitions hosted from the boat. The three stages are
# the places the owner named; the flow is where Lyric Show is on screen; the
# facts are Paradise Busker's own, cited to the white paper and the game plan.
# None of it is priced here — it is the setting the studio sells the app in.

# The three performance zones, from the helm aft. (key, name, where, holds,
# camera, note)
STAGES: list[dict] = [
    {"key": "deck", "name": "Cockpit deck", "where": "aft deck under the hardtop",
     "holds": "one or two performers, seated or standing",
     "camera": "hardtop camera looking aft; the helm Mac within cable reach",
     "note": "the sheltered stage: rain, sun, wind"},
    {"key": "swim_platform", "name": "Swim platform", "where": "the transom, at the waterline",
     "holds": "a solo busker with the water behind them",
     "camera": "dock camera looking forward",
     "note": "the postcard shot; the iD4 cable runs through the transom door"},
    {"key": "dock", "name": "Rear dock", "where": "the finger pier behind the transom",
     "holds": "the crowd, the queue, and the next act",
     "camera": "deck camera looking aft over the platform",
     "note": "where attendees stand, scan the QR, and vote"},
]

# Facts from the sources, nothing estimated. Each figure's section is in
# `notes`, keyed like the fact it cites, so a reader can check the paper.
PARADISE_BUSKER: dict = {
    "source": "Paradise Busker / BNDS white paper (Phygital-DevOps/White Paper Paradise "
              "Busker.txt); the Paradise Busker deck and business report (Elements: "
              "Paradise-Busker.pptx, Paradise Busker .docx, Presentation Text.pages, 2024–2025); "
              "Phygital Verification Tokenization Minting Cash X 2.txt (Artiverse, Nov 2025); "
              "Key West Treasure Hunt GAMEPLAN.md §1 and §4; the KEY WEST, PARADISE BUSKER "
              "commemorative coin (Paradise Busker Coin.png)",
    "competition": {
        "format": "Blind original-song round: the songwriter uploads the song with lyrics and "
                  "chords, another artist records their version, the songwriter approves it; "
                  "fans hear both without knowing who wrote it",
        "votes": ["who wrote it", "best performance", "save it for a playlist"],
        "vote_price_cents": 100,
        "vote_weight": "Treasures collected in person strengthen the vote",
        "grand_prize_share": 0.33,
        "prize_note": "33% of the vote pool is the Grand Prize; the remainder splits between "
                      "the performers",
        "under_13_note": "Free voting during the year a fan turns 13",
    },
    "tipping_artist_share": 0.80,
    "tipping_table_note": "§3.2 text and §6.1: artists retain 80% across tips, merchandise and "
                          "publishing, the platform 20%; Table 1 in §3.2 lists tipping at a "
                          "100% artist share. Both are cited; the 80% in the text is used.",
    "voting": "BNDS token, quadratic",
    "token_price_cents": 100,
    "treasures": {
        "proof": ["QR scan", "the phone mic's acoustic fingerprint of the venue", "GPS",
                  "timestamp"],
        "tiers": ["common", "rare", "legendary"],
        "note": "Proof of attendance is collected in person; the Artiverse spec states that "
                "nothing is ever streamed live — the livestream is the broadcast layer, never "
                "the verification layer",
    },
    "venue_rule": "Any performance location is a venue to give away Treasure and Tokens; a "
                  "performing artist's location becomes a temporary venue for token sales",
    "ar_booths": "AR merch booths at the venue; bought in BNDS or fiat",
    "codex": {"score_split": "60/30/10", "threshold": 0.75},
    "membership_cents": {"low": 500, "high": 1000},
    "deck_tiers_cents": {"fan": 333, "artist": 3300, "venue": 33300},
    "tithe_of_net_profit": 0.10,
    "live_busk_target": 200,
    "live_busk_note": "The Artiverse plan ends in a full end-to-end test with a 200-person live "
                      "busk in Key West",
    "coin": "KEY WEST, PARADISE BUSKER commemorative coin — the treasure hunt's currency of "
            "record",
    "series": "Song Swap",
    "game_hook": "Key West Treasure Hunt's own plan makes the boat the marketing engine: devlog "
                 "every milestone, build in public, launch from the boat; the first playtest is "
                 "recruited at the marina; the game has no score yet and wants a trop-rock one",
    "market": "Key West: 50+ live-music venues, 150–200 working musicians, 2.5–3M tourists and "
              "about 1M cruise passengers a year; the Songwriters Festival as a partner event",
    "notes": {
        "competition": "Paradise Busker .docx 'Competition Mechanics' and Presentation Text.pages "
                       "'Return on Investment': blind voting, $1 BANDS a vote, vote power weighted "
                       "by Treasures, 33% of the portal's votes retained for the Grand Prize",
        "tipping_artist_share": "§3.1, §3.2: BNDS tipping during live events and online "
                                "performances; 80% to the artist, 20% to platform operations",
        "voting": "§3.1, §7.1: token holders vote in song competitions (crowd-sourced reality "
                  "shows); quadratic weighting keeps large holders from dominating",
        "token_price": "Paradise-Busker.pptx slide 8: BANDS fixed at $1 per unit, minted and sold "
                       "only in the Paradise Busker universe",
        "treasures": "§3.5, §5.1, §5.2: proof-of-attendance collectibles minted from a QR scan, "
                     "the phone mic's acoustic fingerprint of the venue, GPS and a timestamp; "
                     "§5.4: rarity tiers common / rare / legendary; Artiverse spec Phase 5: "
                     "'nothing is ever streamed live'",
        "venue_rule": "Paradise-Busker.pptx slide 5 and Paradise Busker .docx 'For Artists'",
        "ar_booths": "§3.4, §5.3: augmented-reality merchandise booths at concerts and venues",
        "codex": "§4.1: the Codex Engine scores lyrics 60% objective / 30% contextual / 10% "
                 "sentiment; 75% or higher qualifies for promotion and tipping",
        # Keyed "membership", not "membership_cents": a note is prose, and a
        # key ending in _cents promises an integer.
        "membership": "§6.1: membership US$5–10 a month for premium livestreams and "
                      "reality-show competitions",
        "deck_tiers": "Presentation Text.pages (Oct 2024) and Paradise Busker .docx (June 2025): "
                      "Fan $3.33/mo, Artist $33/mo, Venue $333/mo",
        "tithe_of_net_profit": "§3.2, §6.3: Paradise Busker tithes 10% of net profit to charity",
        "live_busk": "Phygital Verification Tokenization Minting Cash X 2.txt, Week 11 milestone",
        "coin": "Key West Treasure Hunt GAMEPLAN.md §1: the commemorative coin is the "
                "collectible currency-of-record; the pirate-rooster coin PNG",
        "series": "§4.4: Song Swap episodes dramatise the Codex dialogue — submission, council "
                  "feedback, the artist's response, the final performance",
        "game_hook": "KeyWestTreasureHunt GAMEPLAN.md §4 M1 and M6; the project's memory note "
                     "micah-context; M4 audio plan",
        "market": "Research and Market Deep Dive Key West.pages.pdf; Paradise Busker .docx",
    },
}

# One competition night, start to finish. `product` marks the steps where
# Lyric Show is on screen — those are what sell it. The list is data: a step
# is added or reordered here and nowhere else.
COMPETITION_FLOW: list[dict] = [
    # One Paradise Busker round, hosted from the boat. `product` marks the steps
    # where Lyric Show is on screen — that is what sells it. Sources: the Paradise
    # Busker deck/report (blind original-song rounds, $1 BANDS votes weighted by
    # Treasures, 33% Grand Prize, "any performance location is a venue"), the
    # Artiverse verification spec (QR + GPS + hot mic; nothing is ever streamed
    # live), the BNDS white paper (tips, Codex, Song Swap, premium livestreams)
    # and the Key West Treasure Hunt GAMEPLAN (the coin; launch from the boat).
    {"step": "Two versions, live", "role": "blind original-song round",
     "detail": "The songwriter and a cover artist each perform the song from a stage — deck, swim "
               "platform or rear dock. Nobody on the stream or the dock is told who wrote it.",
     "product": False},
    {"step": "Lyric Show captions both", "role": "Pro Broadcast overlay",
     "detail": "The uploaded lyrics and chords bias recognition; live lyrics ride the stream and the "
               "dock screen in the viewer's language. The app is the demo.",
     "product": True},
    {"step": "Two audiences, one overlay", "role": "stream + dock",
     "detail": "Viewers online and the crowd on the pier see the same captions. The boat is a pop-up "
               "venue: any performance location is a venue.",
     "product": False},
    {"step": "Blind vote in BANDS", "role": "$1 a vote · weighted by Treasures",
     "detail": "Who wrote it, best performance, save it for a playlist. The vote prompt and QR ride the "
               "overlay; 33% of the pool is the Grand Prize, the rest splits between the performers.",
     "product": True},
    {"step": "Tip and tokens", "role": "BNDS micropayments",
     "detail": "Tips go 80% to the artist. Token packages sell at the dock, where the boat is the venue "
               "of the night.",
     "product": False},
    {"step": "Proof of presence", "role": "QR · GPS · hot mic — not the stream",
     "detail": "Dock phones scan the performer's QR; the app captures GPS and time and records the song. "
               "The performer mints the die-cast master, the crowd gets numbered editions and Treasures. "
               "Presence is proven in person: the livestream is the broadcast layer, never the proof.",
     "product": True},
    {"step": "The reveal", "role": "on air",
     "detail": "When the vote closes, who wrote it is revealed to both audiences; the Song Swap cut comes "
               "from the same recording.",
     "product": False},
    {"step": "Lyrics → Codex", "role": "Song Swap material",
     "detail": "The caption transcript is the lyric text for the Codex score; 75% or better qualifies the "
               "song for promotion and tipping.",
     "product": True},
    {"step": "The coin", "role": "Key West Treasure Hunt",
     "detail": "The night's winner takes a KEY WEST, PARADISE BUSKER coin — the game's currency of "
               "record — and the round's Treasure strengthens every voter's next vote. The game launches "
               "from the boat: build in public, devlog every milestone.",
     "product": False},
]

# --- partner streams --------------------------------------------------------
# Other stages the overlay is on. Each partner is a row of data: who they are,
# whether they have committed (counted by default) or are hypothetical (counted
# only when `partners_include_hypothetical` is 1), how often they stream, which
# assumption holds their audience figure, and how much of that audience is
# abroad — a declared share, not a measurement. Committed partners first. An
# artist's audience per stream is subscribers × `partner_live_share`; a
# church's is its live viewers per stream, counted directly. Exactly one of
# the two `_key` fields is set per row; the other is None.
PARTNERS: list[dict] = [
    {"key": "church", "name": "Partner church",
     "handle": "Sunday live streams", "kind": "church",
     "status": "committed", "streams_per_month": 4,
     "subscribers_key": None,
     "live_viewers_per_stream_key": "partner_church_live_viewers",
     "international_share": 0.15,
     "note": "Has the OBS overlay and will caption its Sunday live streams; its live "
             "audience is not on this machine — the placeholder figure must be replaced "
             "with the channel's analytics"},
    {"key": "artist", "name": "Partner artist",
     "handle": "a 6.19M-subscriber channel · YouTube", "kind": "artist",
     "status": "hypothetical", "streams_per_month": 1,
     "subscribers_key": "partner_artist_subscribers",
     "live_viewers_per_stream_key": None,
     "international_share": 0.70,
     "note": "6.19M YouTube subscribers (as given, 2026-08-22); Spanish-language artist with "
             "an audience across Latin America, the US diaspora and Spain — a live set with "
             "Conversation-Mode captions is a bilingual demo"},
]
PARTNER_STATUSES: tuple[str, ...] = ("committed", "hypothetical")
PARTNER_KINDS: tuple[str, ...] = ("church", "artist")

REACH_NOTE = (
    "Captions render in the viewer's own language, so one stream reaches every country "
    "its audience is in; the international shares are declared, not measured"
)
TARGET_NOTE = (
    "Computed at the current rates and partner set"
)

# Systems whose spend the studio inherits. Only A/V and connectivity are counted
# as the studio's capital; the power systems are listed because a show runs on
# them, but they were bought for the boat, not the stream.
INHERITED_SYSTEMS: tuple[str, ...] = (
    "av_security", "connectivity", "solar_generation", "energy_storage",
    "power_conversion", "generator",
)
STUDIO_CAPITAL_SYSTEMS: tuple[str, ...] = ("av_security", "connectivity")
_SYSTEM_NAMES: dict[str, str] = {key: name for key, name, _, _, _ in BOAT_SYSTEMS}

CATALOG_NOTE = "Recorded performances are a catalog; their value is not modeled"
EXCLUDED_LENSES: list[str] = [
    "BNDS tips, votes, token sales and memberships: Paradise Busker's economy, not Lyric Show "
    "revenue — not modeled here",
    "Platform ad-revenue share and tips: depend on partner status the channel does not have",
    "Merchandise and ticketed streams: not modeled",
    "Free-tier users who convert after 30 days: not modeled",
]
UNPRICED_INHERITED_NOTE = (
    "Starlink, the cameras and the sound system are installed per the vessel "
    "specification, but no ledger spend is attributed to A/V or connectivity yet — the "
    "studio's inherited capital is unpriced until the review queue is cleared."
)
BASELINE_NOTE = (
    "Enter the real figure with `okey studio baseline --subscribers N` or project_meta "
    "studio.baseline_subscribers"
)
ROI_NOTE = (
    "Return is modeled from declared rates until shows are recorded; ROI uses show-driven "
    "revenue only."
)


def _a(key: str) -> float:
    return ASSUMPTIONS[key][0]


def _cents(x: float) -> int:
    return int(round(x))


def _frac(num: float, den: float) -> float | None:
    """A ratio, or None when the denominator is gone — not 0, not infinity."""
    return round(num / den, 3) if den > 0 else None


# --- assumptions ------------------------------------------------------------


def _number(key: str, raw) -> float | int:
    """Coerce one assumption value, refusing anything that is not a finite number."""
    if isinstance(raw, bool):
        raise ValueError(f"studio assumption '{key}' must be a number, got {raw!r}")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"studio assumption '{key}' must be a number, got {raw!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"studio assumption '{key}' must be finite, got {raw!r}")
    if key in WHOLE_KEYS:
        if value != int(value):
            what = "months" if key in MONTH_KEYS else "subscribers"
            raise ValueError(f"{key} must be a whole number of {what}, got {raw!r}")
        return int(value)
    return value


def _validate(values: dict[str, float]) -> None:
    for key, value in values.items():
        if key in PROBABILITIES:
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{key} must be within [0, 1], got {value:g}")
            if key in CHURN_KEYS and value <= 0:
                raise ValueError(f"{key} must be greater than 0: steady state divides by it")
        elif key in MONTH_KEYS:
            if not 1 <= value <= 120:
                raise ValueError(f"{key} must be between 1 and 120, got {value}")
        elif key in FLAG_KEYS:
            if value not in (0, 1):
                raise ValueError(f"{key} must be exactly 0 or 1, got {value:g}")
        elif key in ZERO_OK:
            if value < 0:
                raise ValueError(f"{key} must be 0 or greater, got {value:g}")
        elif value <= 0:
            raise ValueError(f"{key} must be greater than 0, got {value:g}")
    mix = sum(values[k] for k in MIX_KEYS)
    if abs(mix - 1.0) > 1e-6:
        raise ValueError(f"plan mix must sum to 1.0 across {', '.join(MIX_KEYS)}, got {mix:g}")


def _observation_rule(key: str, value: float) -> str | None:
    """Why an observed value cannot stand in for the assumption, or None if it can.

    The same ranges an override is held to — a rate within [0, 1], a count of
    0 or more — with one difference: a counted zero is an observation (nobody
    watched; nobody came) rather than an error, so counts may be 0 where an
    override must be positive. A value that fails is reported on the recorded
    block and left out of the funnel; the declared value stands.
    """
    if key in PROBABILITIES:
        if not 0.0 <= value <= 1.0:
            return f"{key} must be within [0, 1], got {value:g}"
    elif value < 0:
        return f"{key} must be 0 or greater, got {value:g}"
    return None


def resolve_assumptions(
    db: Database | None, overrides: dict[str, float] | None = None
) -> dict[str, dict]:
    """The assumption table with `project_meta` and explicit overrides applied.

    Resolution order is declared value, then `studio.<key>` in project_meta,
    then the overrides dict — the same ladder `load_spec` climbs. Each entry
    reports where its value came from, so a reader can tell a default from a
    correction from a what-if.

    A malformed project_meta value is skipped like `load_spec` skips one; the
    resolved table is then validated as a whole, so an out-of-range figure from
    either source is an error rather than a quietly wrong report.
    """
    resolved = {
        key: {"value": value, "note": note, "source": "assumed"}
        for key, (value, note) in STUDIO_ASSUMPTIONS.items()
    }
    if db is not None:
        for row in db.query("SELECT key, value FROM project_meta WHERE key LIKE 'studio.%'"):
            key = row["key"].removeprefix("studio.")
            if key not in STUDIO_ASSUMPTIONS:
                continue
            try:
                value = _number(key, row["value"])
            except ValueError:
                continue
            resolved[key].update(value=value, source="meta")
    for key, raw in (overrides or {}).items():
        if key not in STUDIO_ASSUMPTIONS:
            raise ValueError(f"unknown studio assumption '{key}'")
        resolved[key].update(value=_number(key, raw), source="override")
    _validate({key: entry["value"] for key, entry in resolved.items()})
    return resolved


def _values(resolved: dict[str, dict]) -> dict[str, float]:
    return {key: entry["value"] for key, entry in resolved.items()}


# --- power ------------------------------------------------------------------


def _load_name(key: str) -> str:
    return key.removeprefix("load_").removesuffix("_w").capitalize()


def power_budget(a: dict[str, float], spec: dict[str, float]) -> dict:
    """Can the bank run a show, and what does a show cost the solar day?

    Reads the same specification and efficiency assumptions the risk checks
    and the reward capability lens use, so a show's power figures can never
    disagree with the rest of the project about the same battery.
    """
    eff = _a("inverter_efficiency")
    loads = [{"key": key, "name": _load_name(key), "watts": a[key]} for key in LOAD_KEYS]
    studio_w = sum(a[key] for key in LOAD_KEYS)
    dc_w = studio_w / eff
    session_kwh = dc_w * a["show_hours"] / 1000

    usable_kwh = spec["bank_kwh"] * _a("usable_depth_lifepo4")
    ac_w = spec["ac_load_watts"]
    hours_on_bank = usable_kwh * 1000 / dc_w
    hours_with_ac = usable_kwh * 1000 / ((studio_w + ac_w) / eff)

    nameplate_w = spec["solar_panel_count"] * spec["solar_panel_watts_nameplate"]
    psh = _a("peak_sun_hours")
    harvest_low = nameplate_w * _a("flexible_panel_derate_low") * psh / 1000
    harvest_high = nameplate_w * _a("flexible_panel_derate_high") * psh / 1000

    inverter_w = spec["inverter_watts_continuous"]
    generator_leg_w = spec["inverter_voltage_ac"] * spec["generator_circuit_amps"]

    return {
        "loads": loads,
        "studio_w": round(studio_w, 2),
        "dc_w": round(dc_w, 2),
        "show_hours": a["show_hours"],
        "session_kwh": round(session_kwh, 2),
        "usable_kwh": round(usable_kwh, 2),
        "hours_on_bank": round(hours_on_bank, 2),
        "hours_on_bank_with_ac": round(hours_with_ac, 2),
        "shows_on_bank": round(hours_on_bank / a["show_hours"], 2),
        "ac_load_watts": ac_w,
        "nameplate_w": nameplate_w,
        "harvest_low_kwh": round(harvest_low, 2),
        "harvest_high_kwh": round(harvest_high, 2),
        "session_share_of_solar_day": _frac(session_kwh, harvest_high),
        "inverter_watts_continuous": inverter_w,
        "inverter_utilisation": _frac(studio_w, inverter_w),
        "inverter_utilisation_with_ac": _frac(studio_w + ac_w, inverter_w),
        "generator_leg_w": generator_leg_w,
        "generator_utilisation_with_ac": _frac(studio_w + ac_w, generator_leg_w),
    }


# --- uplink -----------------------------------------------------------------


def uplink_budget(a: dict[str, float]) -> dict:
    """Does the Starlink upload clear the encoder bitrate, with headroom?

    "clear" means even the congested low end carries it; "conditional" means
    only an uncongested link does; "blocked" means not even that.
    """
    low, high = a["starlink_upload_mbps_low"], a["starlink_upload_mbps_high"]
    headroom = a["uplink_headroom"]
    profiles = []
    for name, key in (("1080p60", "bitrate_1080p_mbps"), ("2160p30", "bitrate_2160p_mbps")):
        bitrate = a[key]
        required = bitrate * headroom
        margin_low, margin_high = low - required, high - required
        if margin_low >= 0:
            verdict = "clear"
        elif margin_high >= 0:
            verdict = "conditional"
        else:
            verdict = "blocked"
        profiles.append({
            "name": name, "bitrate_mbps": bitrate, "required_mbps": round(required, 2),
            "margin_low_mbps": round(margin_low, 2), "margin_high_mbps": round(margin_high, 2),
            "verdict": verdict,
        })
    return {
        "upload_low_mbps": low,
        "upload_high_mbps": high,
        "headroom": headroom,
        "caption_latency_ms": LYRICSHOW["caption_latency_ms"],
        "profiles": profiles,
    }


# --- funnel -----------------------------------------------------------------


def funnel_inputs(assumptions: dict[str, dict], recorded: dict) -> dict[str, dict]:
    """The funnel inputs, each tagged with where its value came from.

    Recorded beats modeled: once shows are logged, observed viewers per set,
    the observed stream install rate, the observed competition-night multiple
    and the counted dock crowd replace the assumed (or meta) values. An
    explicit override still wins — a what-if is a what-if. An observation
    outside the range an override is held to is never taken, belt and braces
    with `recorded_shows`, which already withholds it and says why.
    """
    inputs = {}
    for key in FUNNEL_INPUT_KEYS:
        value, source = assumptions[key]["value"], assumptions[key]["source"]
        observed = recorded.get(OBSERVABLE[key]) if key in OBSERVABLE else None
        if (recorded["shows"] > 0 and observed is not None and source != "override"
                and _observation_rule(key, observed) is None):
            value, source = observed, "observed"
        inputs[key] = {"value": value, "source": source}
    return inputs


def _partner_audience(v: dict[str, float]) -> list[dict]:
    """Each partner's audience per month, unrounded, and whether it counts.

    A committed partner always counts; a hypothetical one counts only when
    `partners_include_hypothetical` is 1. Every row still carries its full
    figure — what the partner would add — so the page can show a switched-off
    partner's size; `counted` is what actually enters the month's viewers.
    An artist's viewers per stream are subscribers × `partner_live_share`; a
    church's are its live viewers per stream, taken as given. The abroad
    figure is the partner's declared international share of its viewers.
    """
    include = v["partners_include_hypothetical"] == 1
    rows = []
    for partner in PARTNERS:
        active = partner["status"] == "committed" or include
        if partner["kind"] == "artist":
            subscribers = v[partner["subscribers_key"]]
            live = None
            per_stream = subscribers * v["partner_live_share"]
        else:
            subscribers = None
            live = v[partner["live_viewers_per_stream_key"]]
            per_stream = live
        per_month = per_stream * partner["streams_per_month"]
        rows.append(dict(
            partner, active=active, subscribers=subscribers, live_viewers_per_stream=live,
            viewers_per_month=per_month,
            viewers_abroad=per_month * partner["international_share"],
            counted=per_month if active else 0.0,
        ))
    return rows


def _yield_per_viewer(v: dict[str, float]) -> float:
    """New paid subscribers per stream viewer at the current rates: the
    traveler share of a viewer at the traveler install and paid rates, plus
    the performer share at the performer rates. The dock crowd are not
    viewers, so their installs are not in it — this is what one more viewer
    is worth, on any screen, partner stream or boat."""
    return (v["traveler_share"] * v["traveler_viewer_to_install"] * v["traveler_install_to_paid"]
            + (1 - v["traveler_share"]) * v["viewer_to_install"] * v["install_to_paid"])


def _monthly(v: dict[str, float]) -> dict[str, float]:
    """Per-month audience, installs and new paid subscribers, unrounded.

    Two audiences and two segments, kept apart until the end. A stream viewer
    sees the overlay on a screen somewhere; a dock attendee stands in front of
    the captions with the QR on the overlay and a phone in hand, and converts
    on a different rate. Within the stream audience, `traveler_share` of the
    viewers are travelers — the need is Conversation Mode — and the rest are
    performers, each share installing and paying at its own rates. Dock
    attendees are travelers: they had the two-language demo in person, so
    their installs join the traveler share, pay at the traveler rate and land
    on the traveler plan. Installs are reported by audience (stream / dock)
    and by segment (travelers / performers), and new paid subscribers by
    segment, because one blended rate would hide which audience, or which
    need, a number came from. Competition nights add viewers to the stream
    audience (a multiple of a solo set's) and attendees to the dock audience;
    a month with no competition nights collapses back to the single-stream
    funnel, and a traveler share of 0 collapses the stream back to the
    performer-only one.

    Partner streams add a third source of viewers — `viewers_partner`, the
    counted partners' audiences — and they join the stream audience before
    the traveler/performer split, so a partner's viewer installs and pays at
    the same declared rates as a viewer of the boat's own stream. `viewers`
    is therefore stream + event + partner, and `installs_stream` covers every
    non-dock install, partner viewers included; only the viewer count is kept
    apart, so the page can say how many of the month's viewers the partners
    brought. With no partner counted the funnel is the boat's own.
    """
    viewers_stream = v["viewers_per_show"] * v["shows_per_month"]
    viewers_event = (v["viewers_per_show"] * v["event_viewers_multiplier"]
                     * v["events_per_month"])
    viewers_partner = sum(row["counted"] for row in _partner_audience(v))
    viewers = viewers_stream + viewers_event + viewers_partner
    attendees = v["dock_attendees_per_event"] * v["events_per_month"]
    travelers_viewers = viewers * v["traveler_share"]
    performers_viewers = viewers - travelers_viewers
    travelers_from_stream = travelers_viewers * v["traveler_viewer_to_install"]
    installs_performers = performers_viewers * v["viewer_to_install"]
    installs_event = attendees * v["attendee_to_install"]
    installs_stream = travelers_from_stream + installs_performers
    installs_travelers = travelers_from_stream + installs_event
    installs = installs_travelers + installs_performers
    new_paid_travelers = installs_travelers * v["traveler_install_to_paid"]
    new_paid_performers = installs_performers * v["install_to_paid"]
    return {
        "viewers_stream": viewers_stream,
        "viewers_event": viewers_event,
        "viewers_partner": viewers_partner,
        "viewers": viewers,
        "attendees": attendees,
        "installs_stream": installs_stream,
        "installs_event": installs_event,
        "installs": installs,
        "new_paid": new_paid_travelers + new_paid_performers,
        "travelers_viewers": travelers_viewers,
        "performers_viewers": performers_viewers,
        "installs_travelers": installs_travelers,
        "installs_performers": installs_performers,
        "new_paid_travelers": new_paid_travelers,
        "new_paid_performers": new_paid_performers,
    }


def _plan_prices(v: dict[str, float]) -> list[dict]:
    """Each funnel plan with its month priced as subscribers actually pay it.

    A share of each segment pays annually — `traveler_annual_share` on the
    traveler plan, `annual_share` on the performer plans — and their month
    costs the annual price ÷ 12; the rest pay the list monthly price. The
    blended price is rounded to whole cents per plan — the figure the page
    shows is the figure the funnel uses — and the list prices ride along so a
    reader can see both. `mix_share` is the plan's share within its own
    segment: 1 for the traveler plan, the declared mix for a performer plan.
    """
    plans = []
    for key, name, monthly, annual, segment, mix_key in FUNNEL_PLANS:
        share = v["traveler_annual_share"] if segment == "traveler" else v["annual_share"]
        blended = monthly if annual is None else (1 - share) * monthly + share * (annual / 12)
        plans.append({
            "key": key, "name": name, "segment": segment,
            "mix_share": 1.0 if mix_key is None else v[mix_key],
            "price_cents": _cents(blended),
            "list_monthly_cents": monthly, "annual_cents": annual,
        })
    return plans


def _segment_arpu(plans: list[dict]) -> dict[str, float]:
    """Average monthly price of a new subscriber, by segment: the traveler
    plan's blended price for travelers, the mix-weighted blended price for
    performers. Unrounded — the funnel multiplies these before it rounds."""
    return {
        "travelers": sum(p["mix_share"] * p["price_cents"] for p in plans
                         if p["segment"] == "traveler"),
        "performers": sum(p["mix_share"] * p["price_cents"] for p in plans
                          if p["segment"] == "performer"),
    }


def funnel_model(v: dict[str, float], inputs: dict[str, dict]) -> dict:
    """Audience to installs to paid subscribers, per month and over the horizon.

    Two segments run side by side. Each steady state is its segment's new
    paid subscribers over its own churn — the level a constant inflow settles
    at — and the book's steady state is the sum; MRR is each segment's
    subscribers at its own ARPU, added only at the end. The blended ARPU,
    weighted by new paid subscribers across both segments, is the one price
    the plan donut centres on. The trajectory runs the two cohorts separately,
    each at its own churn and ARPU, and starts the book at the declared
    baseline — today's paying subscribers, a mixed book nobody has split by
    need, so it decays at the performer churn and earns the blended ARPU, and
    that choice is stated here rather than hidden. Each row carries the
    totals, the per-segment subscribers and the show-driven figures — the
    baseline's own decayed remainder subtracted — because the studio can only
    claim the subscribers it brought. Cumulative figures are rounded once from
    the float running total, so the column never drifts from the sum of the
    rows by accumulated rounding. Plan shares are derived, not declared: each
    plan's new subscribers over all new paid, so the donut reads the true mix
    across both segments.
    """
    monthly = _monthly(v)
    new_paid_t, new_paid_p = monthly["new_paid_travelers"], monthly["new_paid_performers"]
    new_paid = monthly["new_paid"]
    plans = _plan_prices(v)
    arpu_by_segment = _segment_arpu(plans)
    arpu_t, arpu_p = arpu_by_segment["travelers"], arpu_by_segment["performers"]
    # Blended over all new paid, weighted by segment. With nobody converting
    # there is nothing to weight: the performer ARPU stands as the reference
    # price rather than a division by zero.
    arpu = (new_paid_t * arpu_t + new_paid_p * arpu_p) / new_paid if new_paid > 0 else arpu_p
    keep = 1 - v["store_commission"]
    churn_t, churn_p = v["traveler_monthly_churn"], v["monthly_churn"]
    steady_t, steady_p = new_paid_t / churn_t, new_paid_p / churn_p
    steady = steady_t + steady_p
    mrr_gross = steady_t * arpu_t + steady_p * arpu_p
    mrr_net_t, mrr_net_p = steady_t * arpu_t * keep, steady_p * arpu_p * keep
    mrr_net = mrr_net_t + mrr_net_p

    horizon = int(v["horizon_months"])
    baseline = v["baseline_subscribers"]
    trajectory = []
    subs_t, subs_p, base = 0.0, 0.0, baseline
    cumulative, show_cumulative = 0.0, 0.0
    for month in range(1, horizon + 1):
        subs_t = subs_t * (1 - churn_t) + new_paid_t
        subs_p = subs_p * (1 - churn_p) + new_paid_p
        base = base * (1 - churn_p)
        show_subs = subs_t + subs_p
        subs = show_subs + base
        show_mrr = (subs_t * arpu_t + subs_p * arpu_p) * keep
        mrr = show_mrr + base * arpu * keep
        cumulative += mrr
        show_cumulative += show_mrr
        trajectory.append({
            "month": month, "subscribers": round(subs, 1),
            "subscribers_travelers": round(subs_t, 1),
            "subscribers_performers": round(subs_p, 1),
            "baseline_subscribers": round(base, 1),
            "mrr_net_cents": _cents(mrr), "cumulative_net_cents": _cents(cumulative),
            "show_driven_subscribers": round(show_subs, 1),
            "show_driven_mrr_net_cents": _cents(show_mrr),
            "show_driven_cumulative_net_cents": _cents(show_cumulative),
        })

    by_plan = []
    for plan in plans:
        new_subscribers = (new_paid_t if plan["segment"] == "traveler"
                           else new_paid_p * plan["mix_share"])
        by_plan.append({
            "key": plan["key"], "name": plan["name"], "segment": plan["segment"],
            "mix_share": plan["mix_share"],
            "share": round(new_subscribers / new_paid, 3) if new_paid > 0 else 0.0,
            "price_cents": plan["price_cents"],
            "list_monthly_cents": plan["list_monthly_cents"],
            "annual_cents": plan["annual_cents"],
            "new_subscribers": round(new_subscribers, 2),
        })

    return {
        "inputs": inputs,
        "monthly": {key: round(value, 2) for key, value in monthly.items()},
        "by_plan": by_plan,
        "arpu_gross_cents": _cents(arpu),
        "arpu_by_segment": {key: _cents(value) for key, value in arpu_by_segment.items()},
        "steady_state": {
            "subscribers": round(steady, 1),
            "subscribers_travelers": round(steady_t, 1),
            "subscribers_performers": round(steady_p, 1),
            "mrr_gross_cents": _cents(mrr_gross),
            "mrr_net_cents": _cents(mrr_net),
            "arr_net_cents": _cents(12 * mrr_net),
            "mrr_net_travelers_cents": _cents(mrr_net_t),
            "mrr_net_performers_cents": _cents(mrr_net_p),
        },
        "trajectory": trajectory,
        "horizon_months": horizon,
    }


# --- reach and the target ---------------------------------------------------


def reach_of(v: dict[str, float], inputs: dict[str, dict]) -> dict:
    """Who the overlay is in front of each month, partner by partner.

    Every partner row carries its full audience — what it adds, or would add
    — and whether it is counted; the totals sum only the counted ones. A
    partner's new paid subscribers a month is its counted viewers at the
    same per-viewer yield every other viewer converts at, so the row and the
    funnel can never disagree about what a partner is worth. `placeholder`
    is True while a partner's audience figure is still the declared stand-in
    — the note in the assumptions table begins with the word — and turns
    False the moment the owner enters the figure through project_meta or an
    override. The abroad figures are the declared international shares of
    the counted viewers, and the note says they are declared. The languages
    are the app's own counts: the reason one stream reaches many countries.
    """
    per_viewer = _yield_per_viewer(v)
    rows = _partner_audience(v)
    partners = []
    for row in rows:
        audience_key = row["subscribers_key"] or row["live_viewers_per_stream_key"]
        partners.append({
            "key": row["key"], "name": row["name"], "handle": row["handle"],
            "kind": row["kind"], "status": row["status"], "active": row["active"],
            "streams_per_month": row["streams_per_month"],
            "subscribers": row["subscribers"],
            "live_viewers_per_stream": row["live_viewers_per_stream"],
            "placeholder": (audience_key in PLACEHOLDERS
                            and inputs[audience_key]["source"] == "assumed"),
            "viewers_per_month": round(row["viewers_per_month"], 2),
            "international_share": row["international_share"],
            "viewers_abroad": round(row["viewers_abroad"], 2),
            "new_paid_per_month": round(row["counted"] * per_viewer, 2),
            "note": row["note"],
        })
    monthly = _monthly(v)
    return {
        "partners": partners,
        "viewers_partner_per_month": round(monthly["viewers_partner"], 2),
        "viewers_abroad_per_month": round(
            sum(row["viewers_abroad"] for row in rows if row["active"]), 2),
        # The boat's own audience plus the counted partners': the month's viewers.
        "viewers_total_per_month": round(monthly["viewers"], 2),
        "languages": dict(LYRICSHOW["languages"]),
        "note": REACH_NOTE,
    }


def target_of(v: dict[str, float], funnel: dict) -> dict:
    """The owner's target, read against the trajectory and answered in its
    own terms.

    Where the book stands in the target month is the trajectory's own rounded
    row — baseline included, because a subscriber already paying is real —
    and the month the target is reached is the first rounded row at or above
    it, so the table and this block can never disagree; None means not
    within the horizon, and a target month past the horizon has no row to
    read. What it would take is closed-form: the new paid subscribers a month
    that, from a standing start, stack to the target by the target month —
    target ÷ Σ (1 − churn)^i over the months — at the TRAVELER churn, because
    travelers are nearly all of the book and the blend would change with
    every slider; and the stream viewers a month that yield them at the
    current rates, which is None when the current rates convert nobody,
    rather than a division by zero. A target of 0 is no target: the block is
    still present, the book's standing in the month is still read, and every
    figure that needs a target to mean anything is None.
    """
    target, month = v["target_subscribers"], int(v["target_month"])
    rows = funnel["trajectory"]
    at_month = rows[month - 1]["subscribers"] if month <= len(rows) else None
    reached = required_new = required_viewers = shortfall = on_track = None
    if target > 0:
        reached = next((row["month"] for row in rows if row["subscribers"] >= target), None)
        cohort = sum((1 - v["traveler_monthly_churn"]) ** i for i in range(month))
        required_new = target / cohort
        per_viewer = _yield_per_viewer(v)
        required_viewers = required_new / per_viewer if per_viewer > 0 else None
        shortfall = None if at_month is None else round(max(0.0, target - at_month), 1)
        on_track = reached is not None and reached <= month
    return {
        "subscribers": target,
        "month": month,
        "subscribers_at_target_month": at_month,
        "reached_month": reached,
        "shortfall": shortfall,
        "required_new_paid_per_month": None if required_new is None else round(required_new, 2),
        "required_viewers_per_month": (None if required_viewers is None
                                       else round(required_viewers, 2)),
        "on_track": on_track,
        "note": TARGET_NOTE,
    }


# --- lenses -----------------------------------------------------------------


def lenses_of(v: dict[str, float], funnel: dict) -> dict:
    """Three views of return that must stay three.

    Subscription revenue is recurring money; acquisition displaced is a cost
    not incurred; the catalog is an asset nobody here can price. Summing them
    would be adding units that do not add.
    """
    # Acquisition displaced counts every install, stream and dock alike, a
    # partner's viewer or the boat's own: a paid campaign would have had to
    # buy each of them.
    installs = _monthly(v)["installs"]
    cpi_cents = _cents(v["paid_cpi_dollars"] * 100)
    steady = funnel["steady_state"]
    month_12 = next((row for row in funnel["trajectory"] if row["month"] == 12), None)
    songs_per_month = v["songs_per_show"] * v["shows_per_month"]
    buskers_per_month = v["buskers_per_event"] * v["events_per_month"]
    return {
        "subscription": {
            "steady_subscribers": steady["subscribers"],
            "steady_mrr_net_cents": steady["mrr_net_cents"],
            "steady_arr_net_cents": steady["arr_net_cents"],
            # The month-12 row, total and show-driven alike; every key means
            # what it means in the trajectory.
            "month_12": None if month_12 is None else {
                key: value for key, value in month_12.items() if key != "month"},
            # The same steady state by segment — two needs, two churns, two
            # prices — so a reader can see which share carries the month. The
            # totals above are their sum; nothing else is added.
            "by_segment": {
                "travelers": {"steady_subscribers": steady["subscribers_travelers"],
                              "steady_mrr_net_cents": steady["mrr_net_travelers_cents"]},
                "performers": {"steady_subscribers": steady["subscribers_performers"],
                               "steady_mrr_net_cents": steady["mrr_net_performers_cents"]},
            },
        },
        "acquisition_displaced": {
            "installs_per_month": round(installs, 2),
            "cpi_cents": cpi_cents,
            "monthly_cents": _cents(installs * cpi_cents),
            "annual_cents": _cents(installs * cpi_cents * 12),
        },
        "catalog": {
            "songs_per_month": round(songs_per_month, 1),
            "songs_per_year": round(songs_per_month * 12, 1),
            "buskers_per_month": round(buskers_per_month, 1),
            "buskers_per_year": round(buskers_per_month * 12, 1),
            "priced": False,
            "note": CATALOG_NOTE,
        },
        "excluded": list(EXCLUDED_LENSES),
    }


# --- breakeven --------------------------------------------------------------


def moorage_monthly(db: Database) -> int | None:
    """Average monthly slip cost from the ledger, or None when there is none.

    Months are counted from the orders actually dated, so a one-off moorage
    charge is not spread across months it did not cover. No dated moorage
    spend means no monthly figure — not a zero.
    """
    row = db.one(
        """SELECT COALESCE(SUM(li.total_cents), 0) AS cents,
                  COUNT(DISTINCT substr(o.ordered_at, 1, 7)) AS months
           FROM line_items li
           JOIN orders o ON o.id = li.order_id
           JOIN boat_systems bs ON bs.id = li.system_id
           WHERE bs.key = 'moorage' AND li.relevance = 'boat'"""
    )
    if row is None or not row["cents"] or not row["months"]:
        return None
    return _cents(row["cents"] / row["months"])


def breakeven_of(db: Database, funnel: dict, kit_planned_cents: int) -> dict:
    """The month each threshold is crossed on the modeled trajectory, or None
    if it is not crossed within the horizon. Read against the rounded rows so
    the table and these months can never disagree — and against the
    show-driven columns, because the studio cannot claim payback from
    subscribers it did not bring; a baseline moves none of these months."""
    rows = funnel["trajectory"]
    project_spend = totals(db)["net_cents"]
    moorage = moorage_monthly(db)

    def first_month(field: str, threshold: int) -> int | None:
        return next((row["month"] for row in rows if row[field] >= threshold), None)

    return {
        "project_spend_cents": project_spend,
        "kit_planned_cents": kit_planned_cents,
        "kit_month": first_month("show_driven_cumulative_net_cents", kit_planned_cents),
        # Nothing spent is nothing to pay back — not "paid back in month 1".
        "project_month": (first_month("show_driven_cumulative_net_cents", project_spend)
                          if project_spend > 0 else None),
        "moorage_monthly_cents": moorage,
        "slip_month": (None if moorage is None
                       else first_month("show_driven_mrr_net_cents", moorage)),
        "horizon_months": funnel["horizon_months"],
    }


# --- roi ------------------------------------------------------------------------


def _ratio(num: float, den: float) -> float | None:
    """A ratio at two places, or None when the denominator is gone."""
    return round(num / den, 2) if den > 0 else None


def roi_of(v: dict[str, float], funnel: dict, breakeven: dict) -> dict:
    """What the kit returns, read off the show-driven trajectory.

    Every figure here is derived from the subscription lens — it is not a
    fourth lens and adds nothing to the other three. Cumulative return is
    show-driven, the baseline's own revenue left out, so a large baseline
    cannot flatter the kit. A ratio whose denominator is nothing is None, not
    0 and not infinity: no shows means no per-show figure, no installs means
    no cost per install, nothing spent means no share of spend.
    """
    rows = funnel["trajectory"]
    kit = breakeven["kit_planned_cents"]
    project_spend = breakeven["project_spend_cents"]
    mrr_net = funnel["steady_state"]["mrr_net_cents"]
    monthly = funnel["monthly"]
    month_12 = next((row for row in rows if row["month"] == 12), None)
    last = rows[-1]
    shows = v["shows_per_month"] + v["events_per_month"]

    def at(row: dict) -> dict:
        cumulative = row["show_driven_cumulative_net_cents"]
        return {"show_driven_cumulative_net_cents": cumulative,
                "roi_multiple_on_kit": _ratio(cumulative, kit)}

    return {
        "kit_cents": kit,
        "month_12": None if month_12 is None else at(month_12),
        "horizon": dict(at(last), share_of_project_spend=_ratio(
            last["show_driven_cumulative_net_cents"], project_spend)),
        "per_show_net_cents": _cents(mrr_net / shows) if shows > 0 else None,
        "per_viewer_net_cents": (
            _cents(mrr_net / monthly["viewers"]) if monthly["viewers"] > 0 else None),
        # What the kit costs per install in year one, to set against the paid CPI.
        "cost_per_install_cents": (
            _cents(kit / (monthly["installs"] * 12)) if monthly["installs"] > 0 else None),
        "payback": {field: breakeven[field] for field in ("kit_month", "slip_month",
                                                         "project_month")},
        "note": ROI_NOTE,
    }


# --- recorded shows ---------------------------------------------------------


def recorded_shows(db: Database) -> dict:
    """What the shows actually did. Sums stay None while every row is blank,
    because "no installs were attributed" and "nobody wrote the number down"
    are different statements.

    Each observed figure is drawn from the rows that can honestly support it,
    and says how many that was:

    * viewers per show — the average over solo sets with a viewer count. A
      competition night's audience is the multiplied one, so it is kept out
      of the per-set average and averaged on its own as viewers per night;
      the ratio of the two is the observed competition multiple, which
      exists only when both kinds have been counted.
    * the stream install rate — installs over viewers, over solo sets where
      BOTH were written down. A competition night's installs include the dock
      crowd's (nobody at the dock counts those apart), and a row with one
      count but not the other would pair installs from one show with viewers
      from another; neither belongs in a rate.
    * the dock crowd — averaged over the competition nights where someone
      counted it; SQLite's AVG skips NULLs, and a plain set with a few people
      on the pier is not a competition crowd.

    The unfiltered totals (every viewer and install logged, whatever the kind)
    are kept for the record. An observation outside the range an override is
    held to — a negative count, a rate above 1 — is withheld from the funnel
    and listed under `ignored_observations` with the reason, so a bad row can
    never drive the model negative and the reader can see that it tried.
    """
    both = "kind = 'set' AND unique_viewers IS NOT NULL AND installs_attributed IS NOT NULL"
    agg = db.one(
        f"""SELECT COUNT(*) AS shows,
                   SUM(CASE WHEN kind = 'competition' THEN 1 ELSE 0 END) AS competitions,
                   SUM(unique_viewers) AS viewers,
                   SUM(installs_attributed) AS installs,
                   AVG(CASE WHEN kind = 'set' THEN unique_viewers END) AS set_avg_viewers,
                   COUNT(CASE WHEN kind = 'set' THEN unique_viewers END) AS set_viewer_rows,
                   AVG(CASE WHEN kind = 'competition' THEN unique_viewers END)
                       AS event_avg_viewers,
                   COUNT(CASE WHEN kind = 'competition' THEN unique_viewers END)
                       AS event_viewer_rows,
                   SUM(CASE WHEN {both} THEN installs_attributed END) AS rate_installs,
                   SUM(CASE WHEN {both} THEN unique_viewers END) AS rate_viewers,
                   COUNT(CASE WHEN {both} THEN 1 END) AS rate_rows,
                   AVG(CASE WHEN kind = 'competition' THEN attendees END) AS avg_attendees,
                   COUNT(CASE WHEN kind = 'competition' THEN attendees END) AS attendee_rows
            FROM show_log"""
    )

    def col(name: str):
        return None if agg is None else agg[name]

    def count(name: str) -> int:
        return int(col(name) or 0)

    ignored: list[dict] = []

    def accept(key: str, field: str, value: float | None) -> float | None:
        """An observation the model may take, or None — with the reason listed."""
        if value is None:
            return None
        reason = _observation_rule(key, value)
        if reason is None:
            return value
        ignored.append({"key": key, "field": field, "value": value,
                        "reason": f"{reason}; the declared value stands"})
        return None

    viewers = None if col("viewers") is None else int(col("viewers"))
    installs = None if col("installs") is None else int(col("installs"))
    set_avg = None if col("set_avg_viewers") is None else round(col("set_avg_viewers"), 1)
    event_avg = (None if col("event_avg_viewers") is None
                 else round(col("event_avg_viewers"), 1))
    rate = (round(col("rate_installs") / col("rate_viewers"), 3)
            if col("rate_viewers") is not None and col("rate_viewers") > 0 else None)
    attendees = (None if col("avg_attendees") is None else round(col("avg_attendees"), 1))

    viewers_per_show = accept("viewers_per_show", "observed_viewers_per_show", set_avg)
    event_viewers = accept("event_viewers_multiplier", "observed_event_viewers_per_night",
                           event_avg)
    multiplier = (round(event_viewers / viewers_per_show, 3)
                  if viewers_per_show is not None and viewers_per_show > 0
                  and event_viewers is not None else None)
    rows = db.query(
        """SELECT performed_at, kind, platform, title, duration_minutes, peak_viewers,
                  unique_viewers, attendees, installs_attributed, note
           FROM show_log ORDER BY performed_at DESC, id DESC LIMIT 20"""
    )
    return {
        "shows": count("shows"),
        "competitions": count("competitions"),
        "unique_viewers": viewers,
        "installs": installs,
        "observed_viewers_per_show": viewers_per_show,
        "viewers_counted_shows": count("set_viewer_rows"),
        "observed_event_viewers_per_night": event_viewers,
        "event_viewers_counted_nights": count("event_viewer_rows"),
        "observed_event_multiplier": multiplier,
        "observed_viewer_to_install": accept("viewer_to_install", "observed_viewer_to_install",
                                             rate),
        "install_rate_shows": count("rate_rows"),
        "observed_attendees_per_event": accept("dock_attendees_per_event",
                                               "observed_attendees_per_event", attendees),
        "attendees_counted_nights": count("attendee_rows"),
        "ignored_observations": ignored,
        "rows": [dict(row) for row in rows],
    }


# --- kit and inherited capital ----------------------------------------------


def kit_budget(db: Database) -> dict:
    """The planned purchase list, and whatever the ledger says is already bought."""
    where = " OR ".join("lower(description) LIKE ?" for _ in KIT_KEYWORDS)
    rows = db.query(
        f"SELECT description, total_cents FROM line_items "
        f"WHERE relevance = 'boat' AND ({where}) ORDER BY id",
        [f"%{word}%" for word in KIT_KEYWORDS],
    )
    recorded = [{"description": r["description"], "cents": int(r["total_cents"])} for r in rows]
    return {
        "planned": [{"name": name, "cents": cents, "note": note}
                    for name, cents, note in STUDIO_KIT],
        "planned_cents": sum(cents for _, cents, _ in STUDIO_KIT),
        "recorded": recorded,
        "recorded_cents": sum(item["cents"] for item in recorded),
    }


def inherited_capital(db: Database) -> dict:
    """What the studio gets for free from the refit, and what of it is priced.

    Only A/V and connectivity spend is counted as the studio's capital. The
    power systems are listed because a show runs on them, but they were bought
    for the boat, and charging them to the stream would double-count.
    """
    row = db.one("SELECT value FROM project_meta WHERE key = 'recent_additions'")
    text = row["value"] if row and row["value"] else VESSEL_META["recent_additions"]
    installed = [item.strip() for item in text.split(", ") if item.strip()]

    marks = ", ".join("?" for _ in INHERITED_SYSTEMS)
    spend = {
        r["key"]: int(r["cents"])
        for r in db.query(
            f"""SELECT bs.key, COALESCE(SUM(li.total_cents), 0) AS cents
                FROM boat_systems bs
                LEFT JOIN line_items li ON li.system_id = bs.id AND li.relevance = 'boat'
                WHERE bs.key IN ({marks}) GROUP BY bs.id""",
            INHERITED_SYSTEMS,
        )
    }
    attributed_cents = sum(spend.get(key, 0) for key in STUDIO_CAPITAL_SYSTEMS)
    return {
        "installed": installed,
        "attributed": [
            {"key": key, "name": _SYSTEM_NAMES[key], "cents": spend.get(key, 0)}
            for key in INHERITED_SYSTEMS
        ],
        "attributed_cents": attributed_cents,
        "note": UNPRICED_INHERITED_NOTE if attributed_cents == 0 else None,
    }


def _vessel(db: Database) -> dict:
    keys = {"name": "vessel_name", "registration_mark": "registration_mark",
            "make_model": "vessel_make_model"}
    meta = {
        r["key"]: r["value"]
        for r in db.query("SELECT key, value FROM project_meta WHERE key IN (?, ?, ?)",
                          tuple(keys.values()))
    }
    # project_meta is seeded from VESSEL_META; before that seed runs, the
    # declared facts are still the facts.
    return {field: meta.get(key) or VESSEL_META.get(key) for field, key in keys.items()}


# --- report -----------------------------------------------------------------


def studio_report(db: Database, overrides: dict[str, float] | None = None) -> dict:
    assumptions = resolve_assumptions(db, overrides)
    values = _values(assumptions)
    recorded = recorded_shows(db)
    inputs = funnel_inputs(assumptions, recorded)
    # Power and uplink read the declared table; only the funnel is observable.
    effective = dict(values, **{key: entry["value"] for key, entry in inputs.items()})
    funnel = funnel_model(effective, inputs)
    kit = kit_budget(db)
    breakeven = breakeven_of(db, funnel, kit["planned_cents"])

    return {
        "vessel": _vessel(db),
        "lyricshow": copy.deepcopy(LYRICSHOW),
        "signal_chain": [dict(stage) for stage in SIGNAL_CHAIN],
        # The setting the app is sold in, not a lens: nothing here is summed
        # into the return. Deep-copied so a caller cannot edit the facts.
        "competition": {
            "stages": copy.deepcopy(STAGES),
            "flow": copy.deepcopy(COMPETITION_FLOW),
            "facts": copy.deepcopy(PARADISE_BUSKER),
        },
        "inherited": inherited_capital(db),
        "kit": kit,
        "power": power_budget(values, load_spec(db)),
        "uplink": uplink_budget(values),
        "funnel": funnel,
        # Who the overlay reaches beyond the boat, and the owner's target read
        # against the trajectory: both computed from the same effective inputs
        # the funnel ran on, so a partner's viewers and the month the target is
        # reached are the funnel's own figures, not a second model.
        "reach": reach_of(effective, inputs),
        "target": target_of(effective, funnel),
        "lenses": lenses_of(effective, funnel),
        "breakeven": breakeven,
        "roi": roi_of(effective, funnel, breakeven),
        # Today's paying subscribers, declared rather than read: the stores
        # and Firestore hold the real figure, and 0 means nobody entered it.
        "baseline": {
            "subscribers": assumptions["baseline_subscribers"]["value"],
            "source": assumptions["baseline_subscribers"]["source"],
            "note": BASELINE_NOTE,
        },
        "recorded": recorded,
        "assumptions": assumptions,
    }
