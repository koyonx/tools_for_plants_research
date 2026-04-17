from functools import lru_cache

from supabase import Client, create_client

from app.core.config import settings


@lru_cache
def get_anon_client() -> Client:
    return create_client(settings.supabase_internal_url, settings.anon_key)


@lru_cache
def get_service_client() -> Client:
    return create_client(settings.supabase_internal_url, settings.service_role_key)
