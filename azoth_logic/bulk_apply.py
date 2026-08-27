"""All-or-nothing bulk writes, via the `public.bulk_apply` database function.

WHY THIS IS NOT A PYTHON LOOP

`/bulk_insert` and `/bulk_update` used to iterate the payload here and issue one
PostgREST request per record. PostgREST wraps each REQUEST in a transaction, so
a 60-record payload was 60 transactions -- a failure at record 40 left 39 rows
written, and after the `/delete_*` commands were retired on 2026-08-27 there was
no way to take them back out from Discord.

Everything is now one RPC: one statement, one transaction. The function raises on
the first bad record, which rolls back everything it did. There is no partial
state to report and no cleanup to do, which is why this module has no
error-accumulation machinery -- a payload either lands whole or not at all.

WHAT STAYS HERE

Shape checks that need no round trip, and the translation of a database error
into something readable in Discord. The table allowlist deliberately lives in
the function ONLY: duplicating it here would give it two places to drift, and the
database is the half that actually enforces it.

See db/migrations/2026-08-27_bulk_apply_transactional.sql in the game repo, and
db/tests/bulk_apply_test.sql for what the function guarantees.
"""
from __future__ import annotations

from supabase_client import supabase

MODES = ("insert", "update")


class BulkApplyError(Exception):
    """A payload was rejected. Nothing was written."""


def _readable(error: Exception) -> str:
    """The useful sentence out of a PostgREST error.

    supabase-py raises an APIError carrying the `raise exception` message from
    plpgsql, but reaches it differently across versions -- attribute on some,
    mapping key on others -- so try both before falling back to `str`.
    """
    message = getattr(error, "message", None)
    if not message and isinstance(error, dict):
        message = error.get("message")
    if not message:
        args = getattr(error, "args", None)
        if args and isinstance(args[0], dict):
            message = args[0].get("message")
    text = str(message or error).strip()

    # The function prefixes every message so it is identifiable in Postgres logs.
    # In Discord that prefix is noise on every line.
    prefix = "bulk_apply: "
    return text[len(prefix):] if text.startswith(prefix) else text


def check(payload, mode: str) -> None:
    """Reject what is obviously malformed before spending a round trip.

    Intentionally shallow. The function re-checks everything here and more, and
    is the authority; this exists so the common typos come back instantly rather
    than as a database error.
    """
    if mode not in MODES:
        raise BulkApplyError(f"mode must be one of {', '.join(MODES)} (got `{mode}`)")

    if not isinstance(payload, dict):
        kind = type(payload).__name__
        raise BulkApplyError(
            f"JSON must be an object keyed by table name, got a {kind}.")

    if not payload:
        raise BulkApplyError("JSON object is empty — nothing to apply.")

    for table, records in payload.items():
        if not isinstance(records, list):
            raise BulkApplyError(
                f"`{table}` must map to a list of records, "
                f"got {type(records).__name__}.")
        if not records:
            raise BulkApplyError(f"`{table}` has an empty record list.")
        for index, entry in enumerate(records):
            if not isinstance(entry, dict):
                raise BulkApplyError(
                    f"`{table}`[{index}] must be an object, "
                    f"got {type(entry).__name__}.")
            if not entry:
                raise BulkApplyError(f"`{table}`[{index}] is empty.")
            if mode == "update" and not entry.get("name"):
                raise BulkApplyError(
                    f"`{table}`[{index}] has no `name` to match on. "
                    f"Updates match by name; use `new_name` to rename.")


def apply(payload, mode: str) -> list[dict]:
    """Apply a whole payload, or none of it.

    Returns one entry per record: `{table, index, name, before, after}`.
    `before` is None for inserts. Raises BulkApplyError if anything was
    rejected, in which case the database is unchanged.
    """
    check(payload, mode)

    try:
        response = supabase.rpc(
            "bulk_apply", {"payload": payload, "mode": mode}).execute()
    except Exception as error:      # noqa: BLE001 - re-raised as BulkApplyError
        raise BulkApplyError(_readable(error)) from error

    results = getattr(response, "data", None)
    if not results:
        # The function raises rather than returning empty, so this means the
        # call did not reach it -- a missing function or a role without EXECUTE.
        raise BulkApplyError(
            "The database returned nothing. Is the `bulk_apply` migration "
            "applied, and is the bot holding the service-role key?")
    return results
