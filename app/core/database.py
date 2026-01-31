"""
Supabase Database Client
"""
from supabase import create_client, Client
from typing import Optional, Dict, Any, List
import logging
from jose import jwt as jose_jwt
from app.core.config import settings

logger = logging.getLogger(__name__)

# Global Supabase clients
_supabase_client: Optional[Client] = None
_supabase_admin_client: Optional[Client] = None


def get_supabase_client() -> Client:
    """Get or create Supabase client.

    Prefers the anon key (respects RLS). Falls back to the service role key
    when the anon key isn't configured so local development doesn't fail with
    confusing PostgREST errors (PGRST301).
    """
    global _supabase_client
    
    if _supabase_client is None:
        api_key = settings.SUPABASE_KEY or settings.SUPABASE_SERVICE_KEY
        
        if not api_key:
            raise ValueError("SUPABASE_KEY or SUPABASE_SERVICE_KEY must be configured")
        
        if not settings.SUPABASE_KEY and settings.SUPABASE_SERVICE_KEY:
            logger.warning(
                "SUPABASE_KEY not set; falling back to SUPABASE_SERVICE_KEY (bypasses RLS). "
                "Set SUPABASE_KEY to re-enable row-level security."
            )
        
        _supabase_client = create_client(
            settings.SUPABASE_URL,
            api_key,
        )
    
    return _supabase_client


def get_supabase_admin_client() -> Client:
    """Get or create Supabase admin client with service role key (bypasses RLS)
    
    WARNING: This client bypasses Row Level Security. Use only for:
    - Admin operations that need full database access
    - Background jobs/webhooks without user JWT tokens
    - System-level operations
    """
    global _supabase_admin_client
    
    if _supabase_admin_client is None:
        if not settings.SUPABASE_SERVICE_KEY:
            raise ValueError("SUPABASE_SERVICE_KEY is required for admin operations")
        _supabase_admin_client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_KEY,  # Service role key - bypasses RLS
        )
    
    return _supabase_admin_client


def set_auth_context(token: str):
    """Set JWT context for RLS"""
    client = get_supabase_client()
    client.postgrest.auth(token)


def get_db_client(org_id: Optional[str] = None) -> Client:
    """
    Get database client with org_id context set for RLS.
    
    Call this at the start of every request with the Clerk org_id from the JWT.
    The RPC set_org_context(org_id) executes the equivalent of:
    SET LOCAL app.current_org_id = org_id
    so RLS policies (clerk_org_id = current_setting('app.current_org_id', true)) apply.
    """
    client = get_supabase_client()
    
    if org_id:
        # Set org_id context via RPC function (must be created in database)
        try:
            # Call RPC function to set org_id context
            # This function should execute: SET LOCAL app.current_org_id = org_id
            client.rpc("set_org_context", {"org_id": org_id}).execute()
            logger.debug(f"Set org_id context: {org_id}")
        except Exception as e:
            # If RPC function doesn't exist yet, log warning but continue
            # This allows gradual migration
            logger.warning(f"Could not set org_id context (RPC function may not exist): {e}")
    
    return client


class DatabaseService:
    """Database service with RLS support and org_id context"""
    
    def __init__(self, token: Optional[str] = None, org_id: Optional[str] = None):
        """
        Initialize database service with optional org_id context.
        
        Args:
            token: JWT token for RLS (legacy)
            org_id: Organization ID to set as context (organization-first approach)
        """
        self.client = get_supabase_client()
        self.org_id = org_id
        
        # Set org_id context if provided
        if org_id:
            self.set_org_context(org_id)
        
        if token:
            self.set_auth(token)
    
    def set_org_context(self, org_id: str):
        """
        Set organization ID context for this database connection.
        Executes: SET LOCAL app.current_org_id = org_id
        
        CRITICAL: Must be called before EVERY query because Supabase HTTP client
        doesn't maintain session state across requests. Each HTTP request uses a
        new connection, so SET LOCAL context is lost.
        """
        try:
            # Call RPC function to set org_id context
            # This must be called before each query since Supabase uses HTTP (no persistent connections)
            result = self.client.rpc("set_org_context", {"org_id": org_id}).execute()
            self.org_id = org_id
            logger.debug(f"Set org_id context: {org_id}")
        except Exception as e:
            # Log error but don't fail - RLS will block if context isn't set, which is safe
            logger.error(f"CRITICAL: Could not set org_id context: {e}. RLS policies will block access.", exc_info=True)
            # Don't raise - let RLS handle security (safer than allowing access without context)
    
    def set_auth(self, token: Optional[str]):
        """Set authentication context if token is a Supabase-issued JWT"""
        if not token or not self._is_supabase_token(token):
            logger.debug("Skipping Supabase auth context: non-Supabase token provided")
            return
        set_auth_context(token)
    
    @staticmethod
    def _is_supabase_token(token: str) -> bool:
        """Check whether the JWT was issued by Supabase"""
        try:
            claims = jose_jwt.get_unverified_claims(token)
            issuer = claims.get("iss", "")
            return issuer.startswith(settings.SUPABASE_URL)
        except Exception:
            return False
    
    # Generic CRUD operations
    def select(self, table: str, filters: Optional[Dict[str, Any]] = None, order_by: Optional[str] = None, limit: Optional[int] = None, offset: Optional[int] = None) -> List[Dict[str, Any]]:
        """Select records from table"""
        # CRITICAL: Re-set org context before each query (Supabase HTTP client doesn't maintain session state)
        if self.org_id:
            self.set_org_context(self.org_id)
        
        # #region debug log
        import json
        try:
            with open(r"d:\Users\Admin\Downloads\Truedy Main\.cursor\debug.log", "a", encoding="utf-8") as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"K","location":"database.py:99","message":"Database select called","data":{"table":table,"filters":filters,"order_by":order_by,"limit":limit,"offset":offset},"timestamp":int(__import__("time").time()*1000)})+"\n")
        except: pass
        # #endregion
        query = self.client.table(table).select("*")
        
        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)
        
        if order_by:
            # Handle "column DESC" or "column ASC" format
            parts = order_by.split()
            col = parts[0]
            descending = True
            if len(parts) > 1:
                descending = parts[1].upper() == "DESC"
            
            query = query.order(col, desc=descending)
        
        # Apply pagination if provided
        if limit is not None:
            query = query.limit(limit)
        if offset is not None:
            query = query.offset(offset)
        
        response = query.execute()
        return response.data if response.data else []
    
    def select_one(self, table: str, filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Select single record"""
        results = self.select(table, filters)
        return results[0] if results else None
    
    def insert(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert record"""
        # CRITICAL: Re-set org context before each query (Supabase HTTP client doesn't maintain session state)
        if self.org_id:
            self.set_org_context(self.org_id)
        
        response = self.client.table(table).insert(data).execute()
        return response.data[0] if response.data else {}
    
    def update(self, table: str, filters: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """Update records"""
        # CRITICAL: Re-set org context before each query (Supabase HTTP client doesn't maintain session state)
        if self.org_id:
            self.set_org_context(self.org_id)
        
        query = self.client.table(table).update(data)
        
        for key, value in filters.items():
            query = query.eq(key, value)
        
        response = query.execute()
        return response.data[0] if response.data else {}
    
    def delete(self, table: str, filters: Dict[str, Any]) -> bool:
        """Delete records"""
        # CRITICAL: Re-set org context before each query (Supabase HTTP client doesn't maintain session state)
        if self.org_id:
            self.set_org_context(self.org_id)
        
        query = self.client.table(table).delete()
        
        for key, value in filters.items():
            query = query.eq(key, value)
        
        response = query.execute()
        return len(response.data) > 0
    
    def count(self, table: str, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count records"""
        # CRITICAL: Re-set org context before each query (Supabase HTTP client doesn't maintain session state)
        if self.org_id:
            self.set_org_context(self.org_id)
        
        query = self.client.table(table).select("*", count="exact")
        
        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)
        
        response = query.execute()
        return response.count if response.count else 0
    
    # Specific table methods
    def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Get client by ID (legacy method - prefer get_client_by_org_id)"""
        return self.select_one("clients", {"id": client_id})
    
    def get_client_by_org_id(self, clerk_org_id: str) -> Optional[Dict[str, Any]]:
        """Get client by Clerk organization ID (organization-first approach)"""
        return self.select_one("clients", {"clerk_organization_id": clerk_org_id})
    
    def get_user_by_clerk_id(self, clerk_user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by clerk_user_id (Clerk authentication ONLY)"""
        return self.select_one("users", {"clerk_user_id": clerk_user_id})
    
    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by Clerk user ID (Clerk ONLY - no Google fallback)"""
        return self.get_user_by_clerk_id(user_id)
    
    def get_voice(self, voice_id: str, org_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get voice by ID.
        
        Args:
            voice_id: Voice UUID
            org_id: Organization ID (uses self.org_id if not provided)
        
        Note: client_id parameter removed - use org_id instead
        """
        effective_org_id = org_id or self.org_id
        if not effective_org_id:
            logger.warning("get_voice called without org_id context")
        filters = {"id": voice_id}
        if effective_org_id:
            filters["clerk_org_id"] = effective_org_id
        return self.select_one("voices", filters)
    
    def get_campaign(self, campaign_id: str, org_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get campaign by ID.
        
        Args:
            campaign_id: Campaign UUID
            org_id: Organization ID (uses self.org_id if not provided)
        
        Note: client_id parameter removed - use org_id instead
        """
        effective_org_id = org_id or self.org_id
        if not effective_org_id:
            logger.warning("get_campaign called without org_id context")
        filters = {"id": campaign_id}
        if effective_org_id:
            filters["clerk_org_id"] = effective_org_id
        return self.select_one("campaigns", filters)
    
    def get_call(self, call_id: str, org_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get call by ID.
        
        Args:
            call_id: Call UUID
            org_id: Organization ID (uses self.org_id if not provided)
        
        Note: client_id parameter removed - use org_id instead
        """
        effective_org_id = org_id or self.org_id
        if not effective_org_id:
            logger.warning("get_call called without org_id context")
        filters = {"id": call_id}
        if effective_org_id:
            filters["clerk_org_id"] = effective_org_id
        return self.select_one("calls", filters)
    
    def get_campaign_contacts(self, campaign_id: str) -> List[Dict[str, Any]]:
        """Get campaign contacts"""
        return self.select("campaign_contacts", {"campaign_id": campaign_id})
    
    def update_campaign_stats(self, campaign_id: str) -> Dict[str, Any]:
        """Update campaign statistics"""
        contacts = self.get_campaign_contacts(campaign_id)
        
        stats = {
            "pending": sum(1 for c in contacts if c.get("status") == "pending"),
            "calling": sum(1 for c in contacts if c.get("status") == "calling"),
            "completed": sum(1 for c in contacts if c.get("status") == "completed"),
            "failed": sum(1 for c in contacts if c.get("status") == "failed"),
        }
        
        return self.update("campaigns", {"id": campaign_id}, {"stats": stats})


class DatabaseAdminService:
    """Database admin service that bypasses RLS using service role key
    
    WARNING: This service bypasses Row Level Security. Use only for:
    - Admin operations that need full database access
    - Background jobs/webhooks without user JWT tokens
    - System-level operations
    """
    
    def __init__(self):
        self.client = get_supabase_admin_client()
    
    # Generic CRUD operations (same as DatabaseService but with admin client)
    def select(self, table: str, filters: Optional[Dict[str, Any]] = None, order_by: Optional[str] = None) -> List[Dict[str, Any]]:
        """Select records from table (bypasses RLS)"""
        query = self.client.table(table).select("*")
        
        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)
        
        if order_by:
            # Handle "column DESC" or "column ASC" format
            parts = order_by.split()
            col = parts[0]
            descending = True
            if len(parts) > 1:
                descending = parts[1].upper() == "DESC"
            
            query = query.order(col, desc=descending)
        
        response = query.execute()
        return response.data if response.data else []
    
    def select_one(self, table: str, filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Select single record (bypasses RLS)"""
        results = self.select(table, filters)
        return results[0] if results else None
    
    def insert(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert record (bypasses RLS)"""
        response = self.client.table(table).insert(data).execute()
        return response.data[0] if response.data else {}
    
    def update(self, table: str, filters: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """Update records (bypasses RLS)"""
        query = self.client.table(table).update(data)
        
        for key, value in filters.items():
            query = query.eq(key, value)
        
        response = query.execute()
        return response.data[0] if response.data else {}
    
    def delete(self, table: str, filters: Dict[str, Any]) -> bool:
        """Delete records (bypasses RLS)"""
        query = self.client.table(table).delete()
        
        for key, value in filters.items():
            query = query.eq(key, value)
        
        response = query.execute()
        return len(response.data) > 0
    
    def count(self, table: str, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count records (bypasses RLS)"""
        query = self.client.table(table).select("*", count="exact")
        
        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)
        
        response = query.execute()
        return response.count if response.count else 0
    
    def bulk_insert(self, table: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Bulk insert records (bypasses RLS) - for large imports"""
        if not records:
            return []
        
        # Supabase supports bulk insert via array
        response = self.client.table(table).insert(records).execute()
        return response.data if response.data else []

