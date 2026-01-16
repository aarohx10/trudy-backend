#!/usr/bin/env python3
"""
Script to verify Ultravox configuration
Run this to check if your Ultravox keys are properly configured
"""
import os
import sys
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent))

try:
    from app.core.config import settings
    from app.services.ultravox import ultravox_client
    import asyncio
    import httpx
except ImportError as e:
    print(f"❌ Error importing modules: {e}")
    print("Make sure you're in the z-backend directory and dependencies are installed")
    sys.exit(1)


def check_env_file():
    """Check if .env file exists and has Ultravox keys"""
    env_path = Path(__file__).parent / ".env"
    
    print("\n" + "="*60)
    print("Ultravox Configuration Checker")
    print("="*60 + "\n")
    
    if not env_path.exists():
        print("❌ .env file not found!")
        print(f"   Expected location: {env_path}")
        print("\n   Please create a .env file in the z-backend directory")
        return False
    
    print(f"✅ .env file found: {env_path}")
    
    # Read .env file
    env_vars = {}
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key.strip()] = value.strip()
    
    # Check for Ultravox keys
    has_api_key = 'ULTRAVOX_API_KEY' in env_vars and env_vars['ULTRAVOX_API_KEY']
    has_base_url = 'ULTRAVOX_BASE_URL' in env_vars and env_vars['ULTRAVOX_BASE_URL']
    
    if has_api_key:
        api_key = env_vars['ULTRAVOX_API_KEY']
        print(f"✅ ULTRAVOX_API_KEY found (length: {len(api_key)})")
        if len(api_key) < 10:
            print("   ⚠️  Warning: API key seems too short")
    else:
        print("❌ ULTRAVOX_API_KEY not found or empty in .env file")
    
    if has_base_url:
        base_url = env_vars['ULTRAVOX_BASE_URL']
        print(f"✅ ULTRAVOX_BASE_URL found: {base_url}")
    else:
        print("⚠️  ULTRAVOX_BASE_URL not found, will use default: https://api.ultravox.ai/v1")
    
    return has_api_key


def check_settings():
    """Check if settings are loaded correctly"""
    print("\n" + "-"*60)
    print("Checking Settings Configuration")
    print("-"*60 + "\n")
    
    if settings.ULTRAVOX_API_KEY:
        print(f"✅ ULTRAVOX_API_KEY loaded (length: {len(settings.ULTRAVOX_API_KEY)})")
    else:
        print("❌ ULTRAVOX_API_KEY not loaded from settings")
        print("   Make sure .env file is in the correct location and restart the server")
        return False
    
    print(f"✅ ULTRAVOX_BASE_URL: {settings.ULTRAVOX_BASE_URL}")
    
    return True


async def test_connection():
    """Test connection to Ultravox API"""
    print("\n" + "-"*60)
    print("Testing Ultravox API Connection")
    print("-"*60 + "\n")
    
    if not settings.ULTRAVOX_API_KEY:
        print("❌ Cannot test connection: ULTRAVOX_API_KEY not configured")
        return False
    
    try:
        # Try to make a simple request (list voices or health check)
        url = f"{settings.ULTRAVOX_BASE_URL}/voices"
        headers = {
            "Authorization": f"Bearer {settings.ULTRAVOX_API_KEY}",
            "Content-Type": "application/json",
        }
        
        print(f"Testing connection to: {url}")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            
            if response.status_code == 200:
                print("✅ Successfully connected to Ultravox API!")
                data = response.json()
                print(f"   Response: {len(data.get('data', []))} voices found")
                return True
            elif response.status_code == 401:
                print("❌ Authentication failed - Invalid API key")
                print("   Please check your ULTRAVOX_API_KEY")
                return False
            elif response.status_code == 404:
                print("⚠️  Endpoint not found - Check ULTRAVOX_BASE_URL")
                print(f"   Current URL: {settings.ULTRAVOX_BASE_URL}")
                return False
            else:
                print(f"❌ Connection failed with status: {response.status_code}")
                print(f"   Response: {response.text[:200]}")
                return False
                
    except httpx.RequestError as e:
        print(f"❌ Network error: {e}")
        print("   Check your internet connection and Ultravox API availability")
        return False
    except Exception as e:
        print(f"❌ Error testing connection: {e}")
        return False


async def main():
    """Main function"""
    # Check .env file
    env_ok = check_env_file()
    
    if not env_ok:
        print("\n" + "="*60)
        print("❌ Configuration incomplete!")
        print("="*60)
        print("\nPlease add the following to your z-backend/.env file:")
        print("\nULTRAVOX_API_KEY=your-ultravox-api-key-here")
        print("ULTRAVOX_BASE_URL=https://api.ultravox.ai/v1")
        sys.exit(1)
    
    # Check settings
    settings_ok = check_settings()
    
    if not settings_ok:
        print("\n" + "="*60)
        print("❌ Settings not loaded correctly!")
        print("="*60)
        print("\nPlease restart your backend server after updating .env file")
        sys.exit(1)
    
    # Test connection
    connection_ok = await test_connection()
    
    # Summary
    print("\n" + "="*60)
    if env_ok and settings_ok and connection_ok:
        print("✅ All checks passed! Ultravox is properly configured.")
        print("="*60)
        sys.exit(0)
    else:
        print("❌ Some checks failed. Please review the errors above.")
        print("="*60)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

