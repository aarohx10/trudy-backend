"""
Unit Test - Auth: Verify verify_clerk_jwt returns the correct org_id for invited members

This test verifies that:
1. verify_clerk_jwt correctly extracts org_id from Clerk JWT
2. If org_id is null (personal workspace), user_id is used as org_id
3. _effective_org_id is set correctly in claims
"""
import pytest
import jwt
from unittest.mock import AsyncMock, patch, MagicMock
from app.core.auth import verify_clerk_jwt, get_current_user
from app.core.exceptions import UnauthorizedError


@pytest.mark.asyncio
async def test_verify_clerk_jwt_with_org_id():
    """Test that verify_clerk_jwt returns correct org_id when org_id is present in token"""
    # Mock JWT token with org_id
    mock_token = "mock_token_with_org"
    mock_claims = {
        "sub": "user_123",
        "org_id": "org_456",
        "email": "user@example.com",
        "iss": "https://clerk.truedy.sendora.ai"
    }
    
    # Mock JWKs
    mock_jwks = {
        "keys": [{
            "kid": "test_kid",
            "kty": "RSA",
            "n": "test_n",
            "e": "AQAB"
        }]
    }
    
    # Mock JWT decode
    with patch('app.core.auth.get_clerk_jwks', new_callable=AsyncMock) as mock_get_jwks, \
         patch('app.core.auth._jwk_to_rsa_public_key') as mock_jwk_to_key, \
         patch('jwt.decode') as mock_decode, \
         patch('jwt.get_unverified_header') as mock_header, \
         patch('app.core.cors.validate_clerk_issuer', return_value=True):
        
        mock_get_jwks.return_value = mock_jwks
        mock_header.return_value = {"kid": "test_kid", "alg": "RS256"}
        mock_jwk_to_key.return_value = "mock_public_key"
        mock_decode.return_value = mock_claims
        
        result = await verify_clerk_jwt(mock_token)
        
        # Verify org_id extraction
        assert result.get("org_id") == "org_456"
        assert result.get("_effective_org_id") == "org_456"
        assert result.get("sub") == "user_123"


@pytest.mark.asyncio
async def test_verify_clerk_jwt_personal_workspace_fallback():
    """Test that verify_clerk_jwt uses user_id as org_id when org_id is null (personal workspace)"""
    # Mock JWT token without org_id (personal workspace)
    mock_token = "mock_token_personal"
    mock_claims = {
        "sub": "user_789",
        "org_id": None,  # Personal workspace - no org
        "email": "solo@example.com",
        "iss": "https://clerk.truedy.sendora.ai"
    }
    
    # Mock JWKs
    mock_jwks = {
        "keys": [{
            "kid": "test_kid",
            "kty": "RSA",
            "n": "test_n",
            "e": "AQAB"
        }]
    }
    
    # Mock JWT decode
    with patch('app.core.auth.get_clerk_jwks', new_callable=AsyncMock) as mock_get_jwks, \
         patch('app.core.auth._jwk_to_rsa_public_key') as mock_jwk_to_key, \
         patch('jwt.decode') as mock_decode, \
         patch('jwt.get_unverified_header') as mock_header, \
         patch('app.core.cors.validate_clerk_issuer', return_value=True):
        
        mock_get_jwks.return_value = mock_jwks
        mock_header.return_value = {"kid": "test_kid", "alg": "RS256"}
        mock_jwk_to_key.return_value = "mock_public_key"
        mock_decode.return_value = mock_claims
        
        result = await verify_clerk_jwt(mock_token)
        
        # CRITICAL: Verify fallback logic - user_id should be used as org_id
        assert result.get("org_id") is None  # Original org_id is None
        assert result.get("_effective_org_id") == "user_789"  # Fallback to user_id
        assert result.get("sub") == "user_789"


@pytest.mark.asyncio
async def test_verify_clerk_jwt_invited_member():
    """Test that verify_clerk_jwt returns correct org_id for invited organization members"""
    # Mock JWT token for invited member
    mock_token = "mock_token_invited"
    mock_claims = {
        "sub": "user_invited_123",
        "org_id": "org_team_456",
        "org_role": "org:member",  # Invited as member
        "email": "invited@example.com",
        "iss": "https://clerk.truedy.sendora.ai"
    }
    
    # Mock JWKs
    mock_jwks = {
        "keys": [{
            "kid": "test_kid",
            "kty": "RSA",
            "n": "test_n",
            "e": "AQAB"
        }]
    }
    
    # Mock JWT decode
    with patch('app.core.auth.get_clerk_jwks', new_callable=AsyncMock) as mock_get_jwks, \
         patch('app.core.auth._jwk_to_rsa_public_key') as mock_jwk_to_key, \
         patch('jwt.decode') as mock_decode, \
         patch('jwt.get_unverified_header') as mock_header, \
         patch('app.core.cors.validate_clerk_issuer', return_value=True):
        
        mock_get_jwks.return_value = mock_jwks
        mock_header.return_value = {"kid": "test_kid", "alg": "RS256"}
        mock_jwk_to_key.return_value = "mock_public_key"
        mock_decode.return_value = mock_claims
        
        result = await verify_clerk_jwt(mock_token)
        
        # Verify org_id is correctly extracted for invited members
        assert result.get("org_id") == "org_team_456"
        assert result.get("_effective_org_id") == "org_team_456"
        assert result.get("org_role") == "org:member"
        assert result.get("sub") == "user_invited_123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
