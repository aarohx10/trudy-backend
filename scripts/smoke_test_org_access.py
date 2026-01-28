"""
Final Smoke Test: Invite a dummy email to an organization, log in, and confirm they can edit an agent created by the admin.

This script verifies:
1. Organization member can access organization data
2. Member can edit agents created by admin
3. Data isolation between organizations works correctly
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import logging
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BACKEND_URL = "http://localhost:8000"  # Update with your backend URL


async def smoke_test_org_access():
    """
    Smoke test for organization access.
    
    Prerequisites:
    1. Create an organization in Clerk
    2. Invite a test user (dummy email) to the organization
    3. Admin creates an agent in the organization
    4. Test user logs in and attempts to edit the agent
    """
    
    print("🧪 Starting Organization Access Smoke Test")
    print("=" * 60)
    
    # Test 1: Admin creates an agent
    print("\n1. Testing Admin Agent Creation...")
    admin_token = input("Enter admin Clerk JWT token: ").strip()
    
    if not admin_token:
        print("❌ Admin token required")
        return False
    
    async with httpx.AsyncClient() as client:
        # Create agent as admin
        agent_data = {
            "name": "Smoke Test Agent",
            "call_template": {
                "voice": "test_voice",
                "greeting": "Hello, this is a smoke test agent."
            }
        }
        
        try:
            response = await client.post(
                f"{BACKEND_URL}/api/v1/agents",
                json=agent_data,
                headers={
                    "Authorization": f"Bearer {admin_token}",
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )
            
            if response.status_code == 201:
                agent = response.json().get("data", {})
                agent_id = agent.get("id")
                print(f"✅ Admin created agent: {agent_id}")
            else:
                print(f"❌ Failed to create agent: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error creating agent: {e}")
            return False
    
    # Test 2: Member accesses agent list
    print("\n2. Testing Member Agent Access...")
    member_token = input("Enter member Clerk JWT token: ").strip()
    
    if not member_token:
        print("❌ Member token required")
        return False
    
    async with httpx.AsyncClient() as client:
        try:
            # List agents as member
            response = await client.get(
                f"{BACKEND_URL}/api/v1/agents",
                headers={
                    "Authorization": f"Bearer {member_token}",
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                agents = response.json().get("data", [])
                print(f"✅ Member can see {len(agents)} agent(s)")
                
                # Verify admin's agent is visible
                admin_agent_found = any(a.get("id") == agent_id for a in agents)
                if admin_agent_found:
                    print(f"✅ Member can see admin's agent: {agent_id}")
                else:
                    print(f"❌ Member cannot see admin's agent")
                    return False
            else:
                print(f"❌ Failed to list agents: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error listing agents: {e}")
            return False
    
    # Test 3: Member edits agent
    print("\n3. Testing Member Agent Edit...")
    async with httpx.AsyncClient() as client:
        try:
            # Update agent as member
            update_data = {
                "name": "Smoke Test Agent - Updated by Member"
            }
            
            response = await client.patch(
                f"{BACKEND_URL}/api/v1/agents/{agent_id}",
                json=update_data,
                headers={
                    "Authorization": f"Bearer {member_token}",
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                updated_agent = response.json().get("data", {})
                print(f"✅ Member successfully edited agent")
                print(f"   New name: {updated_agent.get('name')}")
            else:
                print(f"❌ Failed to edit agent: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error editing agent: {e}")
            return False
    
    # Test 4: Verify org isolation (member cannot see other org's agents)
    print("\n4. Testing Organization Isolation...")
    other_org_token = input("Enter token from different organization (or press Enter to skip): ").strip()
    
    if other_org_token:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{BACKEND_URL}/api/v1/agents",
                    headers={
                        "Authorization": f"Bearer {other_org_token}",
                        "Content-Type": "application/json"
                    },
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    other_org_agents = response.json().get("data", [])
                    admin_agent_in_other_org = any(a.get("id") == agent_id for a in other_org_agents)
                    
                    if not admin_agent_in_other_org:
                        print(f"✅ Organization isolation working - other org cannot see this agent")
                    else:
                        print(f"❌ Organization isolation failed - other org can see this agent")
                        return False
                else:
                    print(f"⚠️  Could not verify isolation: {response.status_code}")
                    
            except Exception as e:
                print(f"⚠️  Error testing isolation: {e}")
    
    print("\n" + "=" * 60)
    print("✅ Smoke Test PASSED - Organization access working correctly!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = asyncio.run(smoke_test_org_access())
    sys.exit(0 if success else 1)
