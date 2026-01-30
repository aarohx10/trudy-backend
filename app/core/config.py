"""
Application Configuration
"""
from pydantic_settings import BaseSettings
from pydantic import model_validator, Field, ConfigDict
from typing import List
import os
from pathlib import Path


class Settings(BaseSettings):
    """Application settings"""
    
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=True,
    )
    
    # Environment
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "dev")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    
    # API
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "Trudy API"
    
    def __init__(self, **kwargs):
        """Initialize settings and parse CORS origins - Dynamic with production support
        
        CORS Configuration Strategy (Bulletproof):
        1. CORS_ORIGINS: Explicit list of all known production and development domains
        2. CORS_WILDCARD_PATTERNS: Regex patterns for Vercel previews and subdomains
        
        Both are checked - if origin matches either, it's allowed.
        """
        # Remove CORS_ORIGINS from kwargs to prevent Pydantic from trying to parse it as JSON
        kwargs.pop("CORS_ORIGINS", None)
        super().__init__(**kwargs)
        
        # ============================================================
        # CORS ORIGINS - Explicit allowlist (most reliable)
        # ============================================================
        # These are checked EXACTLY - origin must match one of these
        origins = [
            # ----- Local Development -----
            "http://localhost:3000",
            "http://localhost:3001",
            "http://localhost:8000",  # Backend direct access
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
            "http://127.0.0.1:8000",
            
            # ----- Production Domains -----
            # Truedy main domains
            "https://trudy.ai",
            "https://www.trudy.ai",
            "https://app.trudy.ai",
            "https://truedy.ai",
            "https://www.truedy.ai",
            "https://app.truedy.ai",
            
            # Sendora hosting (current production)
            "https://truedy.sendora.ai",
            "https://www.truedy.sendora.ai",
            "https://app.truedy.sendora.ai",
            
            # Closi.tech hosting (backend domain)
            "https://truedy.closi.tech",
            "https://www.truedy.closi.tech",
            "https://app.truedy.closi.tech",
            
            # Vercel production domain (common patterns)
            "https://truedy.vercel.app",
            "https://truedy-frontend.vercel.app",
            "https://truedy-main.vercel.app",
        ]
        
        # Add Hetzner domain if configured via environment variable
        hetzner_domain = os.getenv("HETZNER_DOMAIN", "")
        if hetzner_domain and hetzner_domain not in origins:
            origins.append(hetzner_domain)
        
        # Add any additional origins from environment variable (comma-separated)
        extra_origins = os.getenv("CORS_EXTRA_ORIGINS", "")
        if extra_origins:
            for origin in extra_origins.split(","):
                origin = origin.strip()
                if origin and origin not in origins:
                    origins.append(origin)
        
        object.__setattr__(self, "CORS_ORIGINS", origins)
        
        # ============================================================
        # CORS WILDCARD PATTERNS - Regex for dynamic subdomains
        # ============================================================
        # These use Python regex syntax - checked if exact match fails
        # Pattern format: Full regex that will be matched against origin
        object.__setattr__(self, "CORS_WILDCARD_PATTERNS", [
            # Vercel preview deployments (handles all preview URLs)
            # Matches: https://anything.vercel.app or https://anything-anything.vercel.app
            r"https://[a-zA-Z0-9][-a-zA-Z0-9]*\.vercel\.app",
            
            # Specific Vercel preview subdomain pattern requested
            # Matches: https://*-aarohx10.vercel.app
            r"https://[a-zA-Z0-9][-a-zA-Z0-9]*\-aarohx10\.vercel\.app",
            
            # Truedy subdomains
            # Matches: https://anything.truedy.ai
            r"https://[a-zA-Z0-9][-a-zA-Z0-9]*\.truedy\.ai",
            
            # Closi.tech subdomains (temporary hosting)
            # Matches: https://anything.closi.tech
            r"https://[a-zA-Z0-9][-a-zA-Z0-9]*\.closi\.tech",
            
            # Sendora subdomains
            # Matches: https://anything.sendora.ai
            r"https://[a-zA-Z0-9][-a-zA-Z0-9]*\.sendora\.ai",
        ])
    
    
    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    
    # Google OAuth removed - using Clerk ONLY
    
    # Clerk Authentication
    CLERK_SECRET_KEY: str = os.getenv("CLERK_SECRET_KEY", "")
    CLERK_PUBLISHABLE_KEY: str = os.getenv("CLERK_PUBLISHABLE_KEY", "")
    # HARD-CODED: Use custom Clerk domain for production (FORCE - ignores env vars)
    CLERK_JWKS_URL: str = "https://clerk.truedy.sendora.ai/.well-known/jwks.json"
    # HARD-CODED: Issuer must match the Clerk domain (FORCE - ignores env vars)
    CLERK_ISSUER: str = "https://clerk.truedy.sendora.ai"
    CLERK_WEBHOOK_SECRET: str = os.getenv("CLERK_WEBHOOK_SECRET", "")
    
    # File Storage (Hetzner VPS)
    # Default to mounted storage path for Hetzner deployment
    FILE_STORAGE_PATH: str = os.getenv("FILE_STORAGE_PATH", "/mnt/storage")
    FILE_SERVER_URL: str = os.getenv("FILE_SERVER_URL", "https://api.truedy.ai")
    # Storage bucket names for uploads and recordings
    STORAGE_BUCKET_UPLOADS: str = os.getenv("STORAGE_BUCKET_UPLOADS", "trudy-uploads")
    STORAGE_BUCKET_RECORDINGS: str = os.getenv("STORAGE_BUCKET_RECORDINGS", "trudy-recordings")
    
    # Encryption (Hetzner VPS - local encryption)
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")  # 32-byte key or passphrase (will be hashed)
    
    # External APIs
    ULTRAVOX_API_KEY: str = os.getenv("ULTRAVOX_API_KEY", "")
    ULTRAVOX_BASE_URL: str = os.getenv("ULTRAVOX_BASE_URL", "https://api.ultravox.ai")
    TELNYX_API_KEY: str = os.getenv("TELNYX_API_KEY", "")
    TELNYX_CONNECTION_ID: str = os.getenv("TELNYX_CONNECTION_ID", "")  # Trudy's SIP Trunk Connection ID
    TELNYX_APPLICATION_ID: str = os.getenv("TELNYX_APPLICATION_ID", "")  # Trudy's TeXML Application ID
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    
    # Ultravox Tool Settings
    ULTRAVOX_TOOL_SECRET: str = os.getenv("ULTRAVOX_TOOL_SECRET", "")  # Secret for Ultravox tool callbacks (X-Tool-Secret header)
    KB_FETCH_API_KEY: str = os.getenv("KB_FETCH_API_KEY", "")  # API key for KB fetch endpoint (used by Ultravox tools)
    
    # Webhooks
    ULTRAVOX_WEBHOOK_SECRET: str = os.getenv("ULTRAVOX_WEBHOOK_SECRET", "")
    TELNYX_WEBHOOK_SECRET: str = os.getenv("TELNYX_WEBHOOK_SECRET", "")
    WEBHOOK_SIGNING_SECRET: str = os.getenv("WEBHOOK_SIGNING_SECRET", "")
    WEBHOOK_BASE_URL: str = os.getenv("WEBHOOK_BASE_URL", "")  # Base URL for webhook endpoints (e.g., https://api.truedy.ai)
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    ENABLE_DEBUG_LOGGING: bool = os.getenv("ENABLE_DEBUG_LOGGING", "true").lower() == "true"
    ENABLE_DB_LOGGING: bool = os.getenv("ENABLE_DB_LOGGING", "true").lower() == "true"
    LOG_RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "30"))
    
    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
    RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "100"))
    
    # Idempotency
    IDEMPOTENCY_TTL_DAYS: int = int(os.getenv("IDEMPOTENCY_TTL_DAYS", "7"))
    
    # Internal API
    INTERNAL_API_KEY: str = os.getenv("INTERNAL_API_KEY", "")


# Initialize settings with graceful .env file error handling
# On Hetzner VPS, environment variables are set directly, so .env parsing errors are non-fatal
try:
    settings = Settings()
except Exception as e:
    # If .env file parsing fails, try again without .env file
    # This handles cases where Railway creates an empty/invalid .env file
    import logging
    import traceback
    import json
    logger = logging.getLogger(__name__)
    error_details_raw = {
        "error_type": type(e).__name__,
        "error_message": str(e),
        "error_args": e.args if hasattr(e, 'args') else None,
        "error_dict": e.__dict__ if hasattr(e, '__dict__') else None,
        "full_traceback": traceback.format_exc(),
        "operation": "load_settings",
    }
    logger.warning(f"[CONFIG] Failed to load .env file (RAW ERROR): {json.dumps(error_details_raw, indent=2, default=str)}. Continuing with environment variables only.")
    
    # Create a new model_config without env_file
    config_no_env = ConfigDict(
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=True,
    )
    
    # Temporarily replace model_config to skip .env file
    original_config = Settings.model_config
    Settings.model_config = config_no_env
    settings = Settings()
    Settings.model_config = original_config  # Restore original

