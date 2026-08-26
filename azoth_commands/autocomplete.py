import logging

from supabase_helpers import fetch_all, SupabaseError

logger = logging.getLogger(__name__)


def autocomplete_from_table(table_name: str, input: str, column: str = "name", filters: dict = None) -> list[str]:
    """Suggestions for a slash-command autocomplete, matched as a substring.

    Discord autocomplete has no error channel -- a raised exception just yields
    no suggestions, with nothing to tell the user why. So this is the one place
    that catches SupabaseError instead of letting it propagate, and logs it
    loudly to the bot console.

    If an autocomplete is silently empty, CHECK THE CONSOLE. The two usual
    causes are a table the loaded key cannot read, and a table that does not
    exist (`game_stats`, still referenced by the /stats version autocomplete).
    """
    try:
        records = fetch_all(table_name, [column], filters)
    except SupabaseError as e:
        logger.error("autocomplete on `%s`.`%s` failed: %s", table_name, column, e)
        print(f"AUTOCOMPLETE FAILED on `{table_name}`.`{column}`: {e}")
        return []

    matches = [row[column] for row in records if column in row and input.lower() in row[column].lower()]
    return sorted(matches, key=lambda s: s.lower())
