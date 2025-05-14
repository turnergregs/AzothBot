import os
from supabase import create_client, Client
from dotenv import load_dotenv
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Missing Supabase credentials! Make sure SUPABASE_URL and SUPABASE_KEY are set.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
