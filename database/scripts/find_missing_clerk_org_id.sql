-- Script: Find missing clerk_org_id values using relationships
-- This script helps identify the correct clerk_org_id for NULL records
-- by checking relationships with other tables that have clerk_org_id set

-- ============================================
-- Step 1: Find NULL agents and their potential org_id from related calls
-- ============================================

SELECT 
    a.id as agent_id,
    a.name as agent_name,
    a.created_at,
    -- Try to find org_id from calls that use this agent
    c.clerk_org_id as suggested_org_id_from_calls,
    COUNT(DISTINCT c.id) as call_count_with_org_id
FROM agents a
LEFT JOIN calls c ON c.agent_id = a.id AND c.clerk_org_id IS NOT NULL
WHERE a.clerk_org_id IS NULL
GROUP BY a.id, a.name, a.created_at, c.clerk_org_id
ORDER BY call_count_with_org_id DESC;

-- Alternative: Get the most common org_id from calls for each agent
SELECT 
    a.id as agent_id,
    a.name as agent_name,
    a.created_at,
    MODE() WITHIN GROUP (ORDER BY c.clerk_org_id) as most_common_org_id_from_calls,
    COUNT(DISTINCT c.clerk_org_id) as unique_orgs_in_calls,
    COUNT(c.id) as total_calls_with_org_id
FROM agents a
LEFT JOIN calls c ON c.agent_id = a.id AND c.clerk_org_id IS NOT NULL
WHERE a.clerk_org_id IS NULL
GROUP BY a.id, a.name, a.created_at
HAVING COUNT(c.id) > 0;

-- ============================================
-- Step 2: Find NULL knowledge_bases and their potential org_id
-- ============================================

-- Check if KBs are linked to agents (through agent.knowledge_bases array field)
-- Note: This is complex because knowledge_bases is stored as an array in agents
-- We'll check if any agent references this KB and has an org_id

SELECT 
    kb.id as kb_id,
    kb.name as kb_name,
    kb.created_at,
    -- Find agents that reference this KB and have org_id
    a.clerk_org_id as suggested_org_id_from_agents,
    COUNT(DISTINCT a.id) as agent_count_with_org_id
FROM knowledge_bases kb
LEFT JOIN agents a ON 
    a.knowledge_bases::text LIKE '%' || kb.id || '%' 
    AND a.clerk_org_id IS NOT NULL
WHERE kb.clerk_org_id IS NULL
GROUP BY kb.id, kb.name, kb.created_at, a.clerk_org_id
ORDER BY agent_count_with_org_id DESC;

-- Alternative: Get most common org_id from agents that reference this KB
SELECT 
    kb.id as kb_id,
    kb.name as kb_name,
    kb.created_at,
    MODE() WITHIN GROUP (ORDER BY a.clerk_org_id) as most_common_org_id_from_agents,
    COUNT(DISTINCT a.clerk_org_id) as unique_orgs_in_agents,
    COUNT(DISTINCT a.id) as total_agents_with_org_id
FROM knowledge_bases kb
LEFT JOIN agents a ON 
    a.knowledge_bases::text LIKE '%' || kb.id || '%' 
    AND a.clerk_org_id IS NOT NULL
WHERE kb.clerk_org_id IS NULL
GROUP BY kb.id, kb.name, kb.created_at
HAVING COUNT(a.id) > 0;

-- ============================================
-- Step 3: Find NULL tools and their potential org_id
-- ============================================

-- Check if tools are linked to agents (through agent.tools array field)
SELECT 
    t.id as tool_id,
    t.name as tool_name,
    t.created_at,
    -- Find agents that reference this tool and have org_id
    a.clerk_org_id as suggested_org_id_from_agents,
    COUNT(DISTINCT a.id) as agent_count_with_org_id
FROM tools t
LEFT JOIN agents a ON 
    a.tools::text LIKE '%' || t.id || '%' 
    AND a.clerk_org_id IS NOT NULL
WHERE t.clerk_org_id IS NULL
GROUP BY t.id, t.name, t.created_at, a.clerk_org_id
ORDER BY agent_count_with_org_id DESC;

-- Alternative: Get most common org_id from agents that reference this tool
SELECT 
    t.id as tool_id,
    t.name as tool_name,
    t.created_at,
    MODE() WITHIN GROUP (ORDER BY a.clerk_org_id) as most_common_org_id_from_agents,
    COUNT(DISTINCT a.clerk_org_id) as unique_orgs_in_agents,
    COUNT(DISTINCT a.id) as total_agents_with_org_id
FROM tools t
LEFT JOIN agents a ON 
    a.tools::text LIKE '%' || t.id || '%' 
    AND a.clerk_org_id IS NOT NULL
WHERE t.clerk_org_id IS NULL
GROUP BY t.id, t.name, t.created_at
HAVING COUNT(a.id) > 0;

-- ============================================
-- Step 4: Check if we can find org_id from users/clients relationship
-- ============================================
-- This checks if there's a way to link through users table

-- First, let's see what organizations exist
SELECT 
    co.clerk_org_id,
    co.name as org_name,
    COUNT(DISTINCT c.id) as client_count
FROM clerk_organizations co
LEFT JOIN clients c ON c.clerk_organization_id = co.clerk_org_id
GROUP BY co.clerk_org_id, co.name
ORDER BY client_count DESC;

-- ============================================
-- Step 5: Show all NULL records with their details
-- ============================================

-- NULL Agents
SELECT 
    'agents' as table_name,
    id,
    name,
    created_at,
    status,
    ultravox_agent_id
FROM agents
WHERE clerk_org_id IS NULL
ORDER BY created_at DESC;

-- NULL Knowledge Bases
SELECT 
    'knowledge_bases' as table_name,
    id,
    name,
    created_at,
    status,
    ultravox_tool_id
FROM knowledge_bases
WHERE clerk_org_id IS NULL
ORDER BY created_at DESC;

-- NULL Tools
SELECT 
    'tools' as table_name,
    id,
    name,
    created_at,
    status,
    ultravox_tool_id
FROM tools
WHERE clerk_org_id IS NULL
ORDER BY created_at DESC;

-- ============================================
-- Step 6: Generate UPDATE statements based on findings
-- ============================================
-- After running the queries above, use the suggested_org_id values
-- to create UPDATE statements like this:

/*
-- Example UPDATE statements (replace with actual IDs from queries above)

-- Update agents based on calls relationship
UPDATE agents 
SET clerk_org_id = 'org_xxxxx'  -- Replace with suggested_org_id_from_calls
WHERE id = 'agent-id-here'      -- Replace with actual agent_id
AND clerk_org_id IS NULL;

-- Update knowledge_bases based on agents relationship
UPDATE knowledge_bases 
SET clerk_org_id = 'org_xxxxx'  -- Replace with suggested_org_id_from_agents
WHERE id = 'kb-id-here'          -- Replace with actual kb_id
AND clerk_org_id IS NULL;

-- Update tools based on agents relationship
UPDATE tools 
SET clerk_org_id = 'org_xxxxx'  -- Replace with suggested_org_id_from_agents
WHERE id = 'tool-id-here'        -- Replace with actual tool_id
AND clerk_org_id IS NULL;
*/
