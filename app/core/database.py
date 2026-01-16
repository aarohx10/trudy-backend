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


class DatabaseService:
    """Database service with RLS support"""
    
    def __init__(self, token: Optional[str] = None):
        self.client = get_supabase_client()
        if token:
            self.set_auth(token)
    
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
    def select(self, table: str, filters: Optional[Dict[str, Any]] = None, order_by: Optional[str] = None) -> List[Dict[str, Any]]:
        """Select records from table"""
        query = self.client.table(table).select("*")
        
        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)
        
        if order_by:
            query = query.order(order_by, desc=True)
        
        response = query.execute()
        return response.data if response.data else []
    
    def select_one(self, table: str, filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Select single record"""
        results = self.select(table, filters)
        return results[0] if results else None
    
    def insert(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert record"""
        response = self.client.table(table).insert(data).execute()
        return response.data[0] if response.data else {}
    
    def update(self, table: str, filters: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        """Update records"""
        query = self.client.table(table).update(data)
        
        for key, value in filters.items():
            query = query.eq(key, value)
        
        response = query.execute()
        return response.data[0] if response.data else {}
    
    def delete(self, table: str, filters: Dict[str, Any]) -> bool:
        """Delete records"""
        query = self.client.table(table).delete()
        
        for key, value in filters.items():
            query = query.eq(key, value)
        
        response = query.execute()
        return len(response.data) > 0
    
    def count(self, table: str, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count records"""
        query = self.client.table(table).select("*", count="exact")
        
        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)
        
        response = query.execute()
        return response.count if response.count else 0
    
    # Specific table methods
    def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Get client by ID"""
        return self.select_one("clients", {"id": client_id})
    
    def get_user_by_clerk_id(self, clerk_user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by clerk_user_id (Clerk authentication ONLY)"""
        return self.select_one("users", {"clerk_user_id": clerk_user_id})
    
    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by Clerk user ID (Clerk ONLY - no Google fallback)"""
        return self.get_user_by_clerk_id(user_id)
    
    def get_voice(self, voice_id: str, client_id: str) -> Optional[Dict[str, Any]]:
        """Get voice by ID"""
        return self.select_one("voices", {"id": voice_id, "client_id": client_id})
    
    def get_agent(self, agent_id: str, client_id: str) -> Optional[Dict[str, Any]]:
        """Get agent by ID"""
        return self.select_one("agents", {"id": agent_id, "client_id": client_id})
    
    def get_knowledge_base(self, kb_id: str, client_id: str) -> Optional[Dict[str, Any]]:
        """Get knowledge base by ID"""
        return self.select_one("knowledge_documents", {"id": kb_id, "client_id": client_id})
    
    def get_campaign(self, campaign_id: str, client_id: str) -> Optional[Dict[str, Any]]:
        """Get campaign by ID"""
        return self.select_one("campaigns", {"id": campaign_id, "client_id": client_id})
    
    def get_call(self, call_id: str, client_id: str) -> Optional[Dict[str, Any]]:
        """Get call by ID"""
        return self.select_one("calls", {"id": call_id, "client_id": client_id})
    
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
            query = query.order(order_by, desc=True)
        
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

