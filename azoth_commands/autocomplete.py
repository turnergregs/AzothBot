from supabase_helpers import fetch_all

def autocomplete_from_table(table_name: str, input: str, column: str = "name", filters: dict = None) -> list[str]:
    records = fetch_all(table_name, [column], filters)
    matches = [row[column] for row in records if column in row and input.lower() in row[column].lower()]
    return sorted(matches, key=lambda s: s.lower())