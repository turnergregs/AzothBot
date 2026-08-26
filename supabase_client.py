import base64
import json
import os
from supabase import create_client, Client
from dotenv import load_dotenv
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Missing Supabase credentials! Make sure SUPABASE_URL and SUPABASE_KEY are set.")


def _decode_key_role(key: str) -> str:
    """Best-effort read of which Supabase role this API key carries.

    Legacy keys are unsigned-readable JWTs with a `role` claim ('anon' or
    'service_role'). Newer keys are opaque `sb_publishable_…` / `sb_secret_…`
    strings. Returns 'unknown' when the key can't be classified — callers must
    treat 'unknown' as "not proven to be service_role".

    This exists because the difference is otherwise INVISIBLE: an anon key does
    not fail on tables it can't read, it returns an empty result set with an
    HTTP 200. See docs/DB_SCHEMA.md § Which key you are holding.
    """
    if key.startswith("sb_secret_"):
        return "service_role"
    if key.startswith("sb_publishable_"):
        return "anon"
    try:
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        role = json.loads(base64.urlsafe_b64decode(payload)).get("role")
        return role if role else "unknown"
    except Exception:
        return "unknown"


SUPABASE_ROLE = _decode_key_role(SUPABASE_KEY)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
