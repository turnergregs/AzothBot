# Deployment

## The honest state

AzothBot runs on **a teammate's Windows machine**, started by hand.

There is no process supervisor, no container, no hosting provider, and no
deployment automation in this repository. **It is not reliably always-on** — when
that machine is off or the process has died, the bot is simply down.

That is a deliberate, proportionate choice. AzothBot is a configuration tool for
a two-person team, not a service with users. It does not get enough use to justify
hosting it. **When it's down, ask the teammate to re-run it.**

Hosting it properly is a future consideration, not current work. Anything below
that reads like a runbook is describing what *would* need to exist, not what does.

## What goes down with it

Worth knowing, since the bot being down is a normal state:

| Capability | Impact |
|---|---|
| All slash commands | Unavailable. Discord shows them as failing to respond |
| `daily_update` reports | Missed. **They catch up on next start** — a startup pass sends any report that was due while the bot was down |
| Bulk ingest | Blocked. Use direct SQL if a content change is urgent |
| Anything the game does | **Unaffected.** The game talks to Supabase directly and does not go through the bot |

The catch-up pass sends at most one report per channel per day; a multi-day outage
does not produce a backlog of reports, only the most recent day's.

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt
python bot.py
```

Success looks like:

```
✅ Logged in as AzothBot#0000
🔁 Synced slash commands to dev guild 000000000000000000
```

Both lines matter. Without the second, the process is connected but no commands
are registered.

Python 3.11 (`.python-version`).

### Directories that have to be there

| Path | Why | If missing |
|---|---|---|
| `eigenfunctions/` | ~95 `.npy`/`.npz` files, loaded at import by the art generator | Startup crash |
| `assets/card_art/` | Vendored borders, symbols and the shader-exported backgrounds the renderer draws from | Renders fail with a `FileNotFoundError` naming the file; the reply says how to restore it |
| `assets/fonts/` | `Aldrich-Regular.ttf` | Every render fails |
| `cache/` | Created on demand | Nothing — it rebuilds |

`cache/` is the render cache (`azoth_logic/art_cache.py`). It is gitignored and
**safe to delete at any time** — deleting it costs the next render of each item
its cached copy, nothing more.

It is **self-limiting**: eviction is size-capped and runs on write, so it cannot
exceed **700 MB** total (art 300 + renders 400). Budget disk for that, not for the
~30 MB it sits at after light use. `/cache status` reports the current figure, and
no periodic cleanup job is needed — that was a deliberate choice, since a timer on
a hand-started bot may not fire for weeks. See
[CARD_RENDERING.md § Eviction](CARD_RENDERING.md#eviction).

### Updating

```bash
git pull
pip install -r requirements.txt   # only if requirements changed
# restart the process
```

> **The 2026-08-26 render rewrite added a dependency.** `opencv-python-headless`
> is imported at module scope by `azoth_logic/eigenfunction_art.py`, which
> `card_render` imports, which the cog imports — so skipping the reinstall on this
> update does not degrade rendering, it stops the bot from starting at all. The
> failure is `ModuleNotFoundError: No module named 'cv2'` before either success
> line prints.

There is nothing else — no build step, no migrations from this side.

## Configuration

Every setting comes from `.env`. Copy `.env.example` and fill it in.

| Variable | Notes |
|---|---|
| `DISCORD_TOKEN` | Discord Developer Portal → your app → Bot → Reset Token |
| `DEV_GUILD_ID` | The one guild commands register to. Every command is scoped to it |
| `AUTHORIZED_USER_IDS` | Comma-separated user IDs. **The security boundary** — see below |
| `SUPABASE_URL` | Project URL |
| `SUPABASE_KEY` | **service_role** on the deployed bot. See below |
| `BOT_PLAYER_ID` | `players` row id written as `created_by` on bot-created content |

`constants.py` calls `int(os.getenv(...))` at import with no guard, so a missing
`DEV_GUILD_ID` or `BOT_PLAYER_ID` crashes at startup with a bare `TypeError` that
doesn't name the variable. `supabase_client.py` at least raises a clear message
for missing Supabase credentials.

### Which Supabase key

**The deployed bot uses the service-role key.** This is load-bearing, not
incidental:

- It is the only way to read `turns`, `turn_nodes` and `levelups`, which are
  INSERT-only for anon.
- It bypasses RLS entirely, so the bot can write content tables.

**Locally, decide deliberately.** With the anon key, analytics tables return zero
rows with an HTTP 200 and every `/stats` command reports "no data" — which is
indistinguishable from an empty database. Full explanation:
[DB_SCHEMA.md § Which key you are holding](DB_SCHEMA.md#azothbot-which-key-you-are-holding).

## Security

Because the deployed bot holds a service-role key, **RLS provides no protection**.
Two things stand between a Discord user and the production database:

1. **`DEV_GUILD_ID`** — commands only exist in one guild.
2. **`AUTHORIZED_USER_IDS`** — checked by `require_authorized` in
   `safe_interaction`. Every mutating command sets it.

Consequences to keep in mind:

- **Anyone in the guild can read.** `/get`, `/render`, `/search`, the deck
  read/render commands and all of `/stats` are unrestricted. `/stats` exposes
  player names and full run history.
- **Anyone on `AUTHORIZED_USER_IDS` can destroy content.** `/delete_card` has no
  confirmation step and no database-level backstop.
- **There are no backups configured from this repo.** Deletions rely on whatever
  Supabase's own retention provides.
- **A new command that writes and forgets `require_authorized=True` is an open
  door.** Check it during review.

### If the token or key leaks

1. Discord Developer Portal → Reset Token.
2. Supabase → Settings → API → roll the service_role key.
3. Update `.env` on the machine running the bot and restart.

Note the **anon** key cannot be rolled independently of the game — it ships inside
the game binary and rolling it breaks every installed copy.

## If it were hosted

Not current work. Recorded so the decision doesn't have to be re-derived.

The bot is a single stateless process apart from `daily_update_state.json`, so
almost any host works. What would need attention:

- **Persist or externalise `daily_update_state.json`.** On an ephemeral filesystem
  it resets on every deploy, and channels lose their registration and dedup date.
- **The bot must be single-instance.** The daily-report deduplication is a local
  file with no locking; two instances would double-send.
- **`eigenfunctions/` must be present.** ~95 `.npy`/`.npz` files, loaded at import.
  A missing directory is a startup crash.
- **`assets/card_art/` must be present too.** Same as `eigenfunctions/`: it is
  vendored art the renderer reads at draw time, and the shader-exported
  backgrounds in it cannot be regenerated without a Godot checkout.
- **Rendering is CPU-bound.** It no longer blocks the event loop — every render
  path goes through `asyncio.to_thread` — but that moves the work to a thread, it
  does not make it cheaper. A `/render_deck` is ~27s of real CPU and download, so
  anything that renders decks regularly wants real CPU, not the smallest instance
  tier.
- **`cache/` wants a persistent disk, and 700 MB of it.** On an ephemeral
  filesystem every deploy throws the render cache away, turning a 0.00s repeat
  render back into 1.77s. It is a cache, so that is a slowdown rather than a
  fault. It evicts itself, so no cleanup job is needed — but size the volume for
  the cap, not for current usage.
