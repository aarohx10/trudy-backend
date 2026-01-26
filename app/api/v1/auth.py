"""
Auth & Client Management Endpoints
"""
from fastapi import APIRouter, Header, Depends
from typing import Optional
from datetime import datetime
import uuid
import logging

from app.core.auth import get_current_user
from app.core.database import DatabaseService
from app.core.encryption import encrypt_api_key, decrypt_api_key
from app.core.exceptions import NotFoundError, ForbiddenError, ConflictError, ValidationError
from app.core.clerk_sync import sync_client_id_to_org_metadata, get_clerk_org_metadata
from app.core.debug_logging import debug_logger
from app.core.api_key_generator import generate_random_api_key
from app.services.ultravox import ultravox_client
from app.models.schemas import (
    UserResponse,
    ClientResponse,
    ApiKeyCreate,
    ApiKeyResponse,
    TTSProviderUpdate,
    ResponseMeta,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/me")
async def get_me(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get current user information, auto-create user/client/organization if doesn't exist"""
    debug_logger.log_request("GET", "/auth/me", {
        "user_id": current_user.get("user_id"),
        "token_type": current_user.get("token_type")
    })
    
    # Use service key for admin operations (creating users/clients)
    from app.core.database import get_supabase_admin_client
    admin_db = get_supabase_admin_client()
    
    # Clerk ONLY - no Google fallback
    token_type = "clerk"
    clerk_org_id = current_user.get("clerk_org_id")
    user_id = current_user["user_id"]
    
    debug_logger.log_step("AUTH_ME", "Processing /auth/me request", {
        "token_type": token_type,
        "clerk_org_id": clerk_org_id,
        "user_id": user_id
    })
    
    user_data = None
    client_id = None
    
    # CRITICAL: Metadata-First Auth - Check Clerk org metadata FIRST (before database lookup)
    # This ensures all team members get the same client_id from org metadata
    if clerk_org_id:
        debug_logger.log_step("AUTH_ME", "Metadata-First: Checking Clerk org metadata for client_id", {"org_id": clerk_org_id})
        
        # STEP 1: Check Clerk org metadata FIRST (SINGLE CLIENT ID POLICY)
        org_metadata = await get_clerk_org_metadata(clerk_org_id)
        if org_metadata and org_metadata.get("public_metadata", {}).get("client_id"):
            metadata_client_id = org_metadata["public_metadata"]["client_id"]
            debug_logger.log_step("AUTH_ME", "Found client_id in Clerk org metadata", {"client_id": metadata_client_id})
            
            # Verify this client exists in database and is linked to this org
            org_client = admin_db.table("clients").select("*").eq("id", metadata_client_id).eq("clerk_organization_id", clerk_org_id).execute()
            if org_client.data:
                client_id = metadata_client_id
                debug_logger.log_step("AUTH_ME", "Using client_id from Clerk org metadata (Metadata-First)", {"client_id": client_id})
            else:
                logger.warning(f"Client {metadata_client_id} from org metadata not found in database or not linked to org {clerk_org_id}")
        
        # STEP 2: Fallback to database lookup if metadata didn't have client_id
        if not client_id:
            debug_logger.log_db("SELECT", "clients", {"filter": "clerk_organization_id", "value": clerk_org_id})
            org_client = admin_db.table("clients").select("*").eq("clerk_organization_id", clerk_org_id).execute()
            if org_client.data:
                client_id = org_client.data[0].get("id")
                debug_logger.log_db("SELECT", "clients", {"result": "found", "client_id": client_id})
                
                # CRITICAL: Sync this client_id to org metadata for future lookups
                await sync_client_id_to_org_metadata(clerk_org_id, client_id)
                debug_logger.log_step("AUTH_ME", "Synced client_id to Clerk org metadata", {"client_id": client_id, "org_id": clerk_org_id})
    
    # Try to find existing user by clerk_user_id
    debug_logger.log_db("SELECT", "users", {"filter": "clerk_user_id", "value": user_id})
    user = admin_db.table("users").select("*").eq("clerk_user_id", user_id).execute()
    user_data = user.data[0] if user.data else None
    
    if user_data:
        debug_logger.log_db("SELECT", "users", {"result": "found", "user_id": user_data.get("id")})
        
        # CRITICAL: If user exists but client_id doesn't match org metadata, update it
        if clerk_org_id and client_id and user_data.get("client_id") != client_id:
            logger.warning(f"User {user_id} has client_id {user_data.get('client_id')} but org metadata says {client_id}. Updating user to match org.")
            debug_logger.log_step("AUTH_ME", "Updating user client_id to match org metadata", {
                "old_client_id": user_data.get("client_id"),
                "new_client_id": client_id
            })
            admin_db.table("users").update({
                "client_id": client_id
            }).eq("clerk_user_id", user_id).execute()
            # Refresh user_data
            user = admin_db.table("users").select("*").eq("clerk_user_id", user_id).execute()
            user_data = user.data[0] if user.data else None
    else:
        debug_logger.log_db("SELECT", "users", {"result": "not_found"})
    
    # If user not found, proceed to create user with client_id from metadata-first check above
    if not user_data:
        debug_logger.log_step("AUTH_ME", "User not found, creating new user/client")
        # First-time login: Create client/organization and user
        if clerk_org_id:
            # Clerk user with organization - sync org with client
            # CRITICAL: If we already have client_id from metadata, use it (don't create new one)
            # Only create if we don't have one yet
            if not client_id:
                # Retry logic to handle race conditions when multiple users join simultaneously
                max_retries = 3
                
                for attempt in range(max_retries):
                    # Check if client exists for this org
                    org_client = admin_db.table("clients").select("*").eq("clerk_organization_id", clerk_org_id).execute()
                    if org_client.data:
                        client_id = org_client.data[0].get("id")
                        debug_logger.log_step("AUTH_ME", "Client exists for org, using existing", {"client_id": client_id, "attempt": attempt + 1})
                        break
                    
                    # Client doesn't exist - wait before retrying (exponential backoff)
                    if attempt < max_retries - 1:
                        import asyncio
                        wait_time = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                        debug_logger.log_step("AUTH_ME", f"Client not found, retrying in {wait_time}s", {"attempt": attempt + 1, "max_retries": max_retries})
                        await asyncio.sleep(wait_time)
                        continue
                    
                    # Last attempt - create client if still doesn't exist
                    debug_logger.log_step("AUTH_ME", "Client not found after retries, creating new client", {"attempt": attempt + 1})
                    # Create new client linked to Clerk organization
                    client_id = str(uuid.uuid4())
                    # Email might be None from JWT - use placeholder if missing
                    email = current_user.get("email") or f"user_{user_id}@placeholder.truedy.ai"
                    client_data = {
                        "id": client_id,
                        "name": current_user.get("name", email.split("@")[0] if email else "New Client"),
                        "email": email,
                        "clerk_organization_id": clerk_org_id,
                        "subscription_status": "active",
                        "credits_balance": 0,
                        "credits_ceiling": 10000,
                    }
                    debug_logger.log_db("INSERT", "clients", {"client_id": client_id, "org_id": clerk_org_id})
                    try:
                        admin_db.table("clients").insert(client_data).execute()
                        logger.info(f"Created new client linked to Clerk org: {client_id}, org: {clerk_org_id}")
                        debug_logger.log_step("AUTH_ME", "Created new client for Clerk org", {"client_id": client_id})
                        break  # Successfully created, exit retry loop
                        break  # Successfully created, exit retry loop
                    except Exception as e:
                        import traceback
                        import json
                        error_details_raw = {
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "error_args": e.args if hasattr(e, 'args') else None,
                            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                            "full_error_object": json.dumps(e.__dict__, default=str) if hasattr(e, '__dict__') else str(e),
                            "full_traceback": traceback.format_exc(),
                            "attempt": attempt,
                            "max_retries": max_retries,
                            "clerk_org_id": clerk_org_id,
                            "client_data": client_data,
                        }
                        logger.error(f"[AUTH] [ME] Client creation error (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
                        
                        # If it's a duplicate key error (23505), fetch the existing client instead
                        error_str = str(e)
                        error_code = None
                        if hasattr(e, 'code'):
                            error_code = e.code
                        elif hasattr(e, 'message') and isinstance(e.message, dict):
                            error_code = e.message.get('code')
                        
                        if "23505" in error_str or error_code == '23505' or "duplicate" in error_str.lower() or "unique" in error_str.lower():
                            logger.info(f"Client creation failed (race condition), fetching existing client for org: {clerk_org_id}")
                            try:
                                # First try to find by org_id (most reliable)
                                org_client = admin_db.table("clients").select("id").eq("clerk_organization_id", clerk_org_id).single().execute()
                                if org_client.data:
                                    client_id = org_client.data["id"]
                                    logger.info(f"Using existing client by org_id: {client_id}")
                                    debug_logger.log_step("AUTH_ME", "Using existing client (race condition resolved)", {"client_id": client_id, "org_id": clerk_org_id})
                                    break  # Successfully found, exit retry loop
                                
                                # Fallback: try to find by email
                                existing_client = admin_db.table("clients").select("id").eq("email", client_data["email"]).single().execute()
                                if existing_client.data:
                                    client_id = existing_client.data["id"]
                                    logger.info(f"Using existing client: {client_id} for email: {client_data['email']}")
                                    debug_logger.log_step("AUTH_ME", "Using existing client (duplicate email)", {"client_id": client_id, "email": client_data["email"]})
                                    break  # Successfully found, exit retry loop
                                
                                # If still not found, this is the last attempt, so raise
                                if attempt == max_retries - 1:
                                    raise e
                            except Exception as fetch_error:
                                import traceback
                                import json
                                fetch_error_details = {
                                    "error_type": type(fetch_error).__name__,
                                    "error_message": str(fetch_error),
                                    "error_args": fetch_error.args if hasattr(fetch_error, 'args') else None,
                                    "error_dict": fetch_error.__dict__ if hasattr(fetch_error, '__dict__') else None,
                                    "full_traceback": traceback.format_exc(),
                                    "clerk_org_id": clerk_org_id,
                                    "client_data_email": client_data.get("email"),
                                }
                                logger.error(f"[AUTH] [ME] Error fetching existing client (RAW ERROR): {json.dumps(fetch_error_details, indent=2, default=str)}", exc_info=True)
                                # If this is the last attempt, raise the original error
                                if attempt == max_retries - 1:
                                    raise e
                                # Otherwise, continue to next retry
                                continue
                        else:
                            # Non-duplicate error - raise immediately
                            raise e
                
                # After retry loop, verify we have client_id
                if not client_id:
                    raise ValueError(f"Failed to get or create client for organization: {clerk_org_id}")
            
            # CRITICAL: Always sync client_id to organization metadata after creation/retrieval
            # This ensures future members get the same client_id from metadata
            if clerk_org_id and client_id:
                debug_logger.log_step("AUTH_ME", "Syncing client_id to Clerk org metadata", {
                    "org_id": clerk_org_id,
                    "client_id": client_id
                })
                sync_success = await sync_client_id_to_org_metadata(clerk_org_id, client_id)
                if sync_success:
                    logger.info(f"Successfully synced client_id {client_id} to Clerk org {clerk_org_id} metadata")
                else:
                    logger.warning(f"Failed to sync client_id {client_id} to Clerk org {clerk_org_id} metadata - will retry on next login")
        else:
            # No organization - create standalone client
            client_id = str(uuid.uuid4())
            # Email might be None from JWT - use placeholder if missing
            email = current_user.get("email") or f"user_{user_id}@placeholder.truedy.ai"
            client_data = {
                "id": client_id,
                "name": current_user.get("name", email.split("@")[0] if email else "New Client"),
                "email": email,
                "subscription_status": "active",
                "credits_balance": 0,
                "credits_ceiling": 10000,
            }
            debug_logger.log_db("INSERT", "clients", {"client_id": client_id})
            try:
                admin_db.table("clients").insert(client_data).execute()
                logger.info(f"Created new client: {client_id}")
                debug_logger.log_step("AUTH_ME", "Created new standalone client", {"client_id": client_id})
            except Exception as e:
                import traceback
                import json
                error_details_raw = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "error_args": e.args if hasattr(e, 'args') else None,
                    "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
                    "full_traceback": traceback.format_exc(),
                    "client_data": client_data,
                }
                logger.error(f"[AUTH] [ME] Error creating standalone client (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
                
                # If it's a duplicate key error (23505), fetch the existing client instead
                error_str = str(e)
                error_code = None
                if hasattr(e, 'code'):
                    error_code = e.code
                elif hasattr(e, 'message') and isinstance(e.message, dict):
                    error_code = e.message.get('code')
                
                if "23505" in error_str or error_code == '23505':
                    logger.info(f"Client already exists for email {client_data['email']}, fetching existing record.")
                    try:
                        existing_client = admin_db.table("clients").select("id").eq("email", client_data["email"]).single().execute()
                        if existing_client.data:
                            client_id = existing_client.data["id"]
                            logger.info(f"Using existing client: {client_id} for email: {client_data['email']}")
                            debug_logger.log_step("AUTH_ME", "Using existing client (duplicate email)", {"client_id": client_id, "email": client_data["email"]})
                        else:
                            raise e
                    except Exception as fetch_error:
                        import traceback
                        import json
                        fetch_error_details = {
                            "error_type": type(fetch_error).__name__,
                            "error_message": str(fetch_error),
                            "error_args": fetch_error.args if hasattr(fetch_error, 'args') else None,
                            "error_dict": fetch_error.__dict__ if hasattr(fetch_error, '__dict__') else None,
                            "full_traceback": traceback.format_exc(),
                            "client_data_email": client_data.get("email"),
                        }
                        logger.error(f"[AUTH] [ME] Error fetching existing standalone client (RAW ERROR): {json.dumps(fetch_error_details, indent=2, default=str)}", exc_info=True)
                        raise e
                else:
                    raise e
        
        # Create user linked to client (Clerk ONLY)
        user_id_uuid = str(uuid.uuid4())
        # Email might be None from JWT - use placeholder if missing
        email = current_user.get("email") or f"user_{user_id}@placeholder.truedy.ai"
        user_data_dict = {
            "id": user_id_uuid,
            "client_id": client_id,
            "email": email,
            "role": "client_admin",  # First user is admin
            "clerk_user_id": user_id,  # Clerk ONLY
            "auth0_sub": "",  # Legacy field - empty string for Clerk-only users (database requires NOT NULL)
        }
        
        debug_logger.log_db("INSERT", "users", {"user_id": user_id_uuid, "client_id": client_id, "token_type": "clerk"})
        admin_db.table("users").insert(user_data_dict).execute()
        logger.info(f"Created new user: {user_id_uuid}, client: {client_id}")
        debug_logger.log_step("AUTH_ME", "Created new user", {"user_id": user_id_uuid, "client_id": client_id})
        
        # Refresh user_data
        user = admin_db.table("users").select("*").eq("clerk_user_id", user_id).execute()
        user_data = user.data[0] if user.data else None
    
    if not user_data:
        raise NotFoundError("user")
    
    # Now use regular database service with user's context
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Refresh user data using Clerk lookup (Clerk ONLY)
    user = db.get_user_by_clerk_id(current_user["user_id"])
    
    if not user:
        debug_logger.log_error("AUTH_ME", NotFoundError("user"), {"user_id": user_id})
        raise NotFoundError("user")
    
    result = {
        "data": UserResponse(**user),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }
    
    debug_logger.log_response("GET", "/auth/me", 200, context={
        "user_id": user.get("id"),
        "client_id": user.get("client_id"),
        "token_type": token_type,
    })
    
    return result


@router.get("/clients")
async def get_clients(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get clients (filtered by role)"""
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    if current_user["role"] == "agency_admin":
        clients = db.select("clients")
    else:
        clients = db.select("clients", {"id": current_user["client_id"]})
    
    return {
        "data": [ClientResponse(**client) for client in clients],
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("/users")
async def get_users(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Get users for the current client (team members)"""
    debug_logger.log_request("GET", "/auth/users", {
        "user_id": current_user.get("user_id"),
        "client_id": current_user.get("client_id"),
    })
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Get users for the current client (RLS will filter by client_id automatically)
    users = db.select("users", {"client_id": current_user["client_id"]})
    
    debug_logger.log_response("GET", "/auth/users", 200, context={
        "user_count": len(users),
        "client_id": current_user.get("client_id"),
    })
    
    return {
        "data": [UserResponse(**user) for user in users],
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.get("/api-keys")
async def list_api_keys(
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """List all API keys for the current client (without decrypted values)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Query api_keys table filtered by client_id
    api_keys = db.select(
        "api_keys",
        {"client_id": current_user["client_id"]},
        order_by="created_at DESC",
    )
    
    # Convert to ApiKeyResponse (never return decrypted keys)
    api_key_responses = [
        ApiKeyResponse(
            id=key["id"],
            client_id=key["client_id"],
            service=key["service"],
            key_name=key["key_name"],
            is_active=key["is_active"],
            created_at=key["created_at"],
        )
        for key in api_keys
    ]
    
    return {
        "data": api_key_responses,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.delete("/api-keys/{api_key_id}")
async def delete_api_key(
    api_key_id: str,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Delete an API key"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Verify the API key exists and belongs to the current user's client_id
    api_key = db.select_one(
        "api_keys",
        {
            "id": api_key_id,
            "client_id": current_user["client_id"],
        },
    )
    
    if not api_key:
        raise NotFoundError("api_key", api_key_id)
    
    # Hard delete from database
    db.delete("api_keys", {"id": api_key_id})
    
    return {
        "data": {
            "api_key_id": api_key_id,
            "deleted": True,
        },
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.post("/api-keys")
async def create_api_key(
    api_key_data: ApiKeyCreate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Create API key (encrypted storage)"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Check for duplicate
    existing = db.select_one(
        "api_keys",
        {
            "client_id": current_user["client_id"],
            "service": api_key_data.service,
            "key_name": api_key_data.key_name,
        },
    )
    if existing:
        raise ConflictError("API key with this name already exists")
    
    # Generate API key if generate flag is set, otherwise use provided key
    if api_key_data.generate:
        api_key_value = generate_random_api_key()
        logger.info(f"Generated random API key for service: {api_key_data.service}, key_name: {api_key_data.key_name}")
    else:
        if not api_key_data.api_key:
            raise ValidationError("API key is required when generate is False")
        api_key_value = api_key_data.api_key
    
    # Encrypt API key
    encrypted_key = encrypt_api_key(api_key_value)
    if not encrypted_key:
        raise ValidationError("Failed to encrypt API key")
    
    # Insert API key
    api_key_record = db.insert(
        "api_keys",
        {
            "client_id": current_user["client_id"],
            "service": api_key_data.service,
            "key_name": api_key_data.key_name,
            "encrypted_key": encrypted_key,
            "settings": api_key_data.settings,
            "is_active": True,
        },
    )
    
    # Return response with decrypted key only for newly generated keys (one-time display)
    response_data = ApiKeyResponse(
        id=api_key_record["id"],
        client_id=api_key_record["client_id"],
        service=api_key_record["service"],
        key_name=api_key_record["key_name"],
        is_active=api_key_record["is_active"],
        created_at=api_key_record["created_at"],
    )
    
    # Include the plaintext key in response if it was generated (for one-time display)
    response_dict = response_data.model_dump()
    if api_key_data.generate:
        response_dict["api_key"] = api_key_value  # Include plaintext for one-time display
    
    return {
        "data": response_dict,
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }


@router.patch("/providers/tts")
async def update_tts_provider(
    provider_data: TTSProviderUpdate,
    current_user: dict = Depends(get_current_user),
    x_client_id: Optional[str] = Header(None),
):
    """Configure external TTS provider"""
    if current_user["role"] not in ["client_admin", "agency_admin"]:
        raise ForbiddenError("Insufficient permissions")
    
    db = DatabaseService(current_user["token"])
    db.set_auth(current_user["token"])
    
    # Update or create API key
    existing = db.select_one(
        "api_keys",
        {
            "client_id": current_user["client_id"],
            "service": provider_data.provider,
        },
    )
    
    # Encrypt API key
    encrypted_key = encrypt_api_key(provider_data.api_key)
    if not encrypted_key:
        raise ValidationError("Failed to encrypt API key")
    
    if existing:
        api_key_record = db.update(
            "api_keys",
            {"id": existing["id"]},
            {
                "encrypted_key": encrypted_key,
                "settings": provider_data.settings,
                "is_active": True,
            },
        )
    else:
        api_key_record = db.insert(
            "api_keys",
            {
                "client_id": current_user["client_id"],
                "service": provider_data.provider,
                "key_name": f"{provider_data.provider.title()} TTS Key",
                "encrypted_key": encrypted_key,
                "settings": provider_data.settings,
                "is_active": True,
            },
        )
    
    # Call Ultravox API to update TTS config
    try:
        logger.info(f"Updating TTS provider configuration in Ultravox: {provider_data.provider}")
        
        # Prepare API key data for Ultravox
        api_key_data = {
            "api_key": provider_data.api_key,
        }
        
        # Add settings if provided
        if provider_data.settings:
            api_key_data.update(provider_data.settings)
        
        # Call Ultravox API
        ultravox_response = await ultravox_client.update_tts_api_key(
            provider=provider_data.provider,
            api_key_data=api_key_data,
        )
        
        logger.info(f"Successfully updated TTS provider configuration in Ultravox: {provider_data.provider}")
        
        # Optionally store Ultravox response metadata
        if ultravox_response:
            logger.debug(f"Ultravox TTS config response: {ultravox_response}")
    except Exception as e:
        import traceback
        import json
        error_details_raw = {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "error_args": e.args if hasattr(e, 'args') else None,
            "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
            "full_traceback": traceback.format_exc(),
            "provider": provider_data.provider if 'provider_data' in locals() else None,
        }
        # Log error but don't fail the request - database update already succeeded
        logger.error(f"[AUTH] [TTS_CONFIG] Failed to update TTS configuration in Ultravox (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}", exc_info=True)
        logger.warning("TTS configuration saved to database but Ultravox update failed. Configuration may not be active in Ultravox.")
    
    return {
        "data": ApiKeyResponse(
            id=api_key_record["id"],
            client_id=api_key_record["client_id"],
            service=api_key_record["service"],
            key_name=api_key_record["key_name"],
            is_active=api_key_record["is_active"],
            created_at=api_key_record["created_at"],
        ),
        "meta": ResponseMeta(
            request_id=str(uuid.uuid4()),
            ts=datetime.utcnow(),
        ),
    }

