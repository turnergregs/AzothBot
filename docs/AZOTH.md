# What Azoth Is

Enough of the game to read this bot's commands and its database schema without
guessing. This is a **summary written for bot developers** — the authority on
every system here is the game repo's `docs/` directory, principally
`GAME_OVERVIEW.md`, `GAME_FLOW.md` and `BOSSES.md`.

Azoth is a roguelike deckbuilder built in Godot by a two-person team: designed by
Caleb Gannon, developed by Caleb Gannon and Turner Gregory.

## The shape of a run

A **run** is one playthrough, permadeath, spanning up to five acts — Awakening,
Illumination, Manifestation, Transcendence, Ascension.

Each act runs on a **three-then-one cadence**: three regular turns of setup, then
a fourth turn that is the act boss. Beating the boss advances the act. This
cadence is why the analytics schema looks the way it does — see
[DB_SCHEMA.md](DB_SCHEMA.md).

**A boss fight IS one turn.** It doesn't end until the player or the boss dies.
Any query that treats a boss turn as comparable to a regular turn is wrong.

### How a run ends

| `games.result` | In-game name | Meaning |
|---|---|---|
| `victory` | AZOTH | Defeated the act 5 boss |
| `no_boss_key` | TRANSCENDENCE | Beat act 4 but never unlocked act 5 — **this counts as a win** |
| `death` | DEFEATED | Life reached 0 |
| `patterned` | OVERWHELMED | Buried by pattern cards |
| `restart` | — | Abandoned, not a terminal outcome |
| `NULL` | — | Quit, crashed, or still playing |

## Links — the distinguishing mechanic

Cards are not played individually for a mana cost. The player combines cards into
a **link**, and the whole link resolves together. A link is valid if it satisfies
at least one link type:

- **Group** — all cards share a valence (three 4s)
- **Set** — all cards share an element (three anima cards)
- **Sequence** — valences form an ascending run (2-3-4)

Links cost **ether**: `max_valence - link_size`, floored at 0. Bigger links are
cheaper. Some card properties bend the rules — `Inert` cards add to link size
without matching, `Transmutable` cards flex element, `Magnify N` makes one card
count as N+1.

## Content types

These map one-to-one onto database tables and onto this bot's CRUD commands.

| Type | Table | What it is |
|---|---|---|
| **Card** | `cards` | The primary object. Element (`blood`/`sol`/`anima`/`default`), valence 1–6, rules text, actions, triggers, properties, subtypes |
| **Aspect** | `aspects` | A permanent effect the player draws on. Lives in an **ordered** zone — order sets trigger firing order. Has `attunement` rather than element/valence |
| **Event** | `events` | A one-shot effect the player holds and spends later. Has `foresight`. Capacity-capped in the events zone |
| **Hero** | `heroes` | Chosen at run start. Has a clickable ability costing life, and an RGB colour |
| **Boss** | `bosses` | HP, damage, a cycling attack **timeline**, and triggers |
| **Deck** | `decks` + `deck_contents` | A named collection. `deck_contents` is a universal join table carrying `content_type` + `content_id` |

"Fate" was the umbrella for aspects and events — the non-card content that goes
into draft packs. It survives as the name of the renderer for those,
`azoth_logic/fate_render.py`, and as the `fates` value that used to live in
`decks.content_type`. That column was dropped 2026-08-27: a deck can hold cards
and fates at once, so a deck-level content type had nothing left to say.

### "Ritual" means two different things — one of them is dead

This has caused real confusion, so be precise:

| | Meaning | Status |
|---|---|---|
| **Ritual (old)** | A challenge/reward content pair, keyed by `challenge_name`. The **precursor to Aspects** | ☠️ **Retired.** The `rituals` table and all its bot commands were removed 2026-08-26 |
| **Ritual (current)** | The game's **difficulty level** — a run-wide setting, stored as `games.ritual` (bigint) | ✅ Live and important |

The current meaning is the one that matters for analytics. **Ritual 5+ injects
Ascender's Bane**, which gates act 4 → 5 — see below. The game loads ritual
definitions from a local file (`assets/game_data/rituals/rituals.json` via
`RitualManager`), *not* from the database, so the dead table was never its
source.

`consumables` was retired at the same time and for the same reason.

## Patterns, and Ascender's Bane

Each act, the game generates a deck of **pattern** cards (also called curses) —
unplayable cards shuffled into the player's deck. They clog the hand and deal
damage at end of turn if left unsolved. The player must solve or purge them. When
the pattern deck runs low, the boss fight begins.

**Ascender's Bane** is a special pattern with outsized analytics importance. It
appears at Ritual 5+, is injected once at run start, and is designed to survive
several acts. Purging it grants the **boss key** — without which a run ending
after act 4 records `no_boss_key` instead of reaching act 5.

It is deliberately **excluded from every ordinary pattern metric**, because
folding it in put a permanent floor under pattern counts and made
"turns that cleared all patterns" read as zero. Its lifecycle is one column:
`turn_nodes.banes_purged`. See [DB_SCHEMA.md § Ascender's Bane](DB_SCHEMA.md#ascenders-bane)
— it has a full section because getting it wrong silently corrupts results.

## Vocabulary you'll meet in the schema

| Term | Meaning |
|---|---|
| **Link** | A set of cards played together; the core action |
| **Node** | One slot on the turn timeline, consumed by a link **or** a skip. "Links played" and "nodes consumed" are different numbers |
| **Skip** | Spending a node to redraw instead of playing. Only during boss fights |
| **Combo** | Score built during link resolution. Grows **exponentially** — stored as an arbitrary-precision BigNum, which is why it's a `text` column and must never be averaged |
| **Valence** | A card's number, 1–6 |
| **Element** | `blood`, `sol`, `anima`, `default` |
| **Attunement** | The aspect equivalent of a stat |
| **Foresight** | How far ahead the player sees the boss timeline; also a field on events/consumables/rituals |
| **Ether** | Spent to play links, regenerates each turn |
| **Draft** | Picking from a pack to add to the deck — the main deckbuilding step |
| **Levelup** | Crossing a combo threshold pays out reward picks from a pack |
| **Act** | One of five chapters, each ending in a boss |
| **Ritual level** | A run difficulty/progression dial. Ritual 5+ injects Ascender's Bane |

## Where content actually lives

**The database is authoritative, not the game repo's JSON files.**

`assets/game_data/` in the game repo is a fallback snapshot exported from
Supabase so the game plays offline and the test suite has fixtures. The running
game pulls content from Supabase.

That is why this bot exists: it is how content gets **into** the authoritative
store. See [CONTENT_PIPELINE.md](CONTENT_PIPELINE.md).

## What never reaches the database

Two populations of play are deliberately not recorded, and you will misread the
data if you forget it:

- **Custom-content runs.** If any custom deck, hero or boss exists, the game sets
  a testing flag at startup and suppresses stats entirely.
- **Headless bot runs.** The game's training bot goes through a separate entry
  point that never reaches the send path.

The dataset is players on stock content only.
