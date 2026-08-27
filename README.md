# AzothBot

Discord bot for **Azoth**, a roguelike deckbuilder built in Godot. AzothBot is
the team's interface to the game's Supabase database: it creates and edits game
content, renders card art, ingests bulk content payloads, and reports gameplay
analytics into Discord.

It is an internal tool for a two-person team, not a public bot. Every command is
registered to a single dev guild.

## What it does

| Area | Commands | Docs |
|---|---|---|
| **Content CRUD** | `create/update/get/delete/render` for cards, aspects, heroes, events, decks | [COMMANDS.md](docs/COMMANDS.md) |
| **Deck curation** | `add_to_deck`, `remove_from_deck`, `stage`, `postpone`, `merge_staging` | [COMMANDS.md](docs/COMMANDS.md#deck-curation) |
| **Bulk ingest** | `bulk_insert`, `bulk_update` from a JSON attachment | [CONTENT_PIPELINE.md](docs/CONTENT_PIPELINE.md) |
| **Analytics** | `/stats` subcommands, scheduled `daily_update` reports | [ANALYTICS.md](docs/ANALYTICS.md) |
| **Rendering** | Procedural card/ritual art from eigenfunction data | [RENDERING.md](docs/RENDERING.md) |

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # then fill it in
python bot.py
```

Commands appear in the dev guild within seconds of `✅ Logged in as …`.

## Read this before touching analytics

The key in your `.env` determines what the bot can see, and the failure is
**silent**: with the anon key, `turns`, `turn_nodes` and `levelups` return zero
rows with an HTTP 200. The deployed bot uses the service-role key and sees
everything. See [DB_SCHEMA.md § Which key you are holding](docs/DB_SCHEMA.md#azothbot-which-key-you-are-holding).

## Documentation

| Doc | Covers |
|---|---|
| [AZOTH.md](docs/AZOTH.md) | What the game is — enough vocabulary to read the schema and the content commands |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Code layout, the cog-attachment pattern, `safe_interaction`, the Supabase helper layer |
| [COMMANDS.md](docs/COMMANDS.md) | Every slash command, its parameters, and what it writes |
| [DB_SCHEMA.md](docs/DB_SCHEMA.md) | Full schema mirror, RLS posture, and the query caveats |
| [ANALYTICS.md](docs/ANALYTICS.md) | The `/stats` views, the daily report, and their known defects |
| [CONTENT_PIPELINE.md](docs/CONTENT_PIPELINE.md) | How content gets from an idea to a database row |
| [CARD_RENDERING.md](docs/CARD_RENDERING.md) | How `/render` draws cards, aspects and rites, and animates their art |
| [RENDERING.md](docs/RENDERING.md) | Legacy renderer: procedural art generation and Supabase Storage |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Where it runs, how to restart it, what to do when it's down |
| [TESTING.md](docs/TESTING.md) | The pytest suite, what it guards, and how it was mutation-tested |

Agent instructions: [AGENTS.md](AGENTS.md) · [CLAUDE.md](CLAUDE.md)

## Related repository

The game itself lives in [`azoth`](https://github.com/turnergregs/azoth). Its
`docs/` directory is the authority on game systems; this repo's docs cover the
bot and mirror only what the bot needs.
