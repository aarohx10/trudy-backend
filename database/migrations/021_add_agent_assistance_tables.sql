-- Migration: Add Agent Assistance Tables
-- Purpose: Enable AI-powered chat assistance for agent creation/editing with session management and prompt suggestions

-- 1. Table for storing AI assistance chat sessions
CREATE TABLE IF NOT EXISTS agent_assistance_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL DEFAULT 'gpt-4o-mini', -- Selected AI model (e.g., "gpt-4o", "gpt-4o-mini")
    title TEXT, -- User-defined or auto-generated session title
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- 2. Table for storing chat messages within sessions
CREATE TABLE IF NOT EXISTS agent_assistance_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES agent_assistance_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    suggested_prompt_change TEXT, -- If AI suggests a prompt change, store it here
    approval_state TEXT CHECK (approval_state IN ('pending', 'approved', 'rejected')), -- Approval state for suggestions
    metadata JSONB DEFAULT '{}'::jsonb, -- Additional data (model used, tokens, etc.)
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);

-- 3. Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_assistance_sessions_agent_id ON agent_assistance_sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_assistance_sessions_client_id ON agent_assistance_sessions(client_id);
CREATE INDEX IF NOT EXISTS idx_assistance_sessions_created_at ON agent_assistance_sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assistance_messages_session_id ON agent_assistance_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_assistance_messages_created_at ON agent_assistance_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_assistance_messages_approval_state ON agent_assistance_messages(approval_state) WHERE approval_state IS NOT NULL;

-- 4. Add updated_at trigger for sessions
CREATE OR REPLACE FUNCTION update_assistance_session_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_assistance_sessions_updated_at 
    BEFORE UPDATE ON agent_assistance_sessions
    FOR EACH ROW EXECUTE FUNCTION update_assistance_session_updated_at();

-- 5. Add comments for documentation
COMMENT ON TABLE agent_assistance_sessions IS 'Stores AI assistance chat sessions for agent creation/editing. Each session represents a conversation thread.';
COMMENT ON TABLE agent_assistance_messages IS 'Stores individual messages within assistance sessions. Includes prompt suggestions and approval states.';
COMMENT ON COLUMN agent_assistance_sessions.model_id IS 'AI model used for this session (e.g., gpt-4o, gpt-4o-mini)';
COMMENT ON COLUMN agent_assistance_messages.suggested_prompt_change IS 'If the assistant message contains a suggested system prompt change, it is stored here';
COMMENT ON COLUMN agent_assistance_messages.approval_state IS 'State of prompt change approval: pending (awaiting user), approved (user accepted), rejected (user declined)';
