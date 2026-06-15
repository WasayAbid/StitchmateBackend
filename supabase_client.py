"""
Supabase client configuration for the backend
"""
import os
from supabase import create_client, Client

# Load environment variables
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://nrcrosiogpecbnhdnbam.supabase.co")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5yY3Jvc2lvZ3BlY2JuaGRuYmFtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM2MzUxNTYsImV4cCI6MjA3OTIxMTE1Nn0.hSs9mQhRwikF8mNHMsqneNbBLQsVtI8vyiS9viDDDUA")
)

# Create and export Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
