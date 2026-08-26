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

### Updating

```bash
git pull
pip install -r requirements.txt   # only if requirements changed
# restart the process
```

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

- **Anyone in the guild can read.** All `get_*`, all `render_*`, and all of
  `/stats` are unrestricted. `/stats` exposes player names and full run history.
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
- **Rendering is CPU-bound** and blocks the event loop. Anything that renders decks
  regularly wants real CPU, not the smallest instance tier.
