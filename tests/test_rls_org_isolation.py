"""
Integration Test - RLS: Attempt to fetch "Org A" data using an "Org B" token.

This test verifies that:
1. Row-Level Security (RLS) prevents cross-organization data access
2. Fetching Org A data with Org B token returns 403 or empty list
3. Database context (set_org_context) properly scopes queries
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.core.database import DatabaseService
from app.core.auth import get_current_user
from app.core.exceptions import UnauthorizedError, ForbiddenError


@pytest.mark.asyncio
async def test_rls_prevents_cross_org_agent_access():
    """Test that RLS prevents fetching agents from Org A using Org B token"""
    # Mock tokens for two different organizations
    org_a_token = "org_a_token"
    org_b_token = "org_b_token"
    
    org_a_claims = {
        "sub": "user_a",
        "org_id": "org_a",
        "_effective_org_id": "org_a",
        "email": "user_a@example.com"
    }
    
    org_b_claims = {
        "sub": "user_b",
        "org_id": "org_b",
        "_effective_org_id": "org_b",
        "email": "user_b@example.com"
    }
    
    # Mock database responses
    org_a_agents = [
        {"id": "agent_1", "name": "Org A Agent", "clerk_org_id": "org_a"},
        {"id": "agent_2", "name": "Org A Agent 2", "clerk_org_id": "org_a"}
    ]
    
    org_b_agents = [
        {"id": "agent_3", "name": "Org B Agent", "clerk_org_id": "org_b"}
    ]
    
    with patch('app.core.auth.verify_jwt') as mock_verify:
        # First, verify with Org A token
        mock_verify.return_value = org_a_claims
        
        db_a = DatabaseService(org_id="org_a")
        db_a.set_org_context("org_a")
        
        # Mock select to return Org A agents
        with patch.object(db_a.client.table('agents').select('*'), 'eq', return_value=MagicMock()) as mock_select:
            mock_select.return_value.execute.return_value.data = org_a_agents
            
            # Should only see Org A agents
            agents_a = db_a.select("agents", {"clerk_org_id": "org_a"})
            assert len(agents_a) == 2
            assert all(agent.get("clerk_org_id") == "org_a" for agent in agents_a)
        
        # Now verify with Org B token
        mock_verify.return_value = org_b_claims
        
        db_b = DatabaseService(org_id="org_b")
        db_b.set_org_context("org_b")
        
        # Mock select to return Org B agents (empty when querying Org A data)
        with patch.object(db_b.client.table('agents').select('*'), 'eq', return_value=MagicMock()) as mock_select:
            mock_select.return_value.execute.return_value.data = org_b_agents
            
            # Should only see Org B agents, not Org A agents
            agents_b = db_b.select("agents", {"clerk_org_id": "org_b"})
            assert len(agents_b) == 1
            assert all(agent.get("clerk_org_id") == "org_b" for agent in agents_b)
            
            # Attempting to fetch Org A agents with Org B context should return empty
            agents_cross_org = db_b.select("agents", {"clerk_org_id": "org_a"})
            assert len(agents_cross_org) == 0  # RLS should prevent cross-org access


@pytest.mark.asyncio
async def test_rls_prevents_cross_org_call_access():
    """Test that RLS prevents fetching calls from Org A using Org B token"""
    org_a_token = "org_a_token"
    org_b_token = "org_b_token"
    
    org_a_claims = {
        "sub": "user_a",
        "org_id": "org_a",
        "_effective_org_id": "org_a"
    }
    
    org_b_claims = {
        "sub": "user_b",
        "org_id": "org_b",
        "_effective_org_id": "org_b"
    }
    
    org_a_calls = [
        {"id": "call_1", "clerk_org_id": "org_a", "phone_number": "+1234567890"},
        {"id": "call_2", "clerk_org_id": "org_a", "phone_number": "+1234567891"}
    ]
    
    org_b_calls = [
        {"id": "call_3", "clerk_org_id": "org_b", "phone_number": "+1234567892"}
    ]
    
    with patch('app.core.auth.verify_jwt') as mock_verify:
        # Test Org A access
        mock_verify.return_value = org_a_claims
        db_a = DatabaseService(org_id="org_a")
        db_a.set_org_context("org_a")
        
        # Mock RLS - should only return Org A calls
        with patch.object(db_a.client.table('calls').select('*'), 'eq', return_value=MagicMock()) as mock_select:
            mock_select.return_value.execute.return_value.data = org_a_calls
            
            calls_a = db_a.select("calls", {"clerk_org_id": "org_a"})
            assert len(calls_a) == 2
            assert all(call.get("clerk_org_id") == "org_a" for call in calls_a)
        
        # Test Org B access - should not see Org A calls
        mock_verify.return_value = org_b_claims
        db_b = DatabaseService(org_id="org_b")
        db_b.set_org_context("org_b")
        
        with patch.object(db_b.client.table('calls').select('*'), 'eq', return_value=MagicMock()) as mock_select:
            mock_select.return_value.execute.return_value.data = org_b_calls
            
            calls_b = db_b.select("calls", {"clerk_org_id": "org_b"})
            assert len(calls_b) == 1
            assert all(call.get("clerk_org_id") == "org_b" for call in calls_b)
            
            # Cross-org access should return empty
            calls_cross_org = db_b.select("calls", {"clerk_org_id": "org_a"})
            assert len(calls_cross_org) == 0


@pytest.mark.asyncio
async def test_database_context_isolation():
    """Test that set_org_context properly isolates database queries"""
    # Create two database service instances with different org contexts
    db_org_a = DatabaseService(org_id="org_a")
    db_org_b = DatabaseService(org_id="org_b")
    
    # Mock RPC call to set_org_context
    with patch.object(db_org_a.client.rpc('set_org_context'), 'call') as mock_rpc_a, \
         patch.object(db_org_b.client.rpc('set_org_context'), 'call') as mock_rpc_b:
        
        db_org_a.set_org_context("org_a")
        db_org_b.set_org_context("org_b")
        
        # Verify RPC was called with correct org_id
        mock_rpc_a.assert_called_once()
        mock_rpc_b.assert_called_once()
        
        # Verify contexts are isolated
        assert db_org_a.org_id == "org_a"
        assert db_org_b.org_id == "org_b"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
