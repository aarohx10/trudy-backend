-- Verification Script: Verify client_id Removal from Main App Tables
-- This script verifies that client_id has been successfully removed from main app tables
-- and that clerk_org_id is properly set up

-- ============================================
-- Step 1: Verify client_id Columns Are Removed
-- ============================================

DO $$
DECLARE
    tables_to_check TEXT[] := ARRAY[
        'agents', 'calls', 'voices', 'knowledge_bases', 
        'tools', 'contacts', 'contact_folders', 'campaigns', 'webhook_endpoints'
    ];
    table_name TEXT;
    has_client_id BOOLEAN;
BEGIN
    FOREACH table_name IN ARRAY tables_to_check
    LOOP
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = table_name 
            AND column_name = 'client_id'
        ) INTO has_client_id;
        
        IF has_client_id THEN
            RAISE EXCEPTION 'Table % still has client_id column!', table_name;
        ELSE
            RAISE NOTICE '✓ Table %: client_id column removed', table_name;
        END IF;
    END LOOP;
END $$;

-- ============================================
-- Step 2: Verify clerk_org_id Columns Exist
-- ============================================

DO $$
DECLARE
    tables_to_check TEXT[] := ARRAY[
        'agents', 'calls', 'voices', 'knowledge_bases', 
        'tools', 'contacts', 'contact_folders', 'campaigns', 'webhook_endpoints'
    ];
    table_name TEXT;
    has_clerk_org_id BOOLEAN;
BEGIN
    FOREACH table_name IN ARRAY tables_to_check
    LOOP
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = table_name 
            AND column_name = 'clerk_org_id'
        ) INTO has_clerk_org_id;
        
        IF NOT has_clerk_org_id THEN
            RAISE EXCEPTION 'Table % missing clerk_org_id column!', table_name;
        ELSE
            RAISE NOTICE '✓ Table %: clerk_org_id column exists', table_name;
        END IF;
    END LOOP;
END $$;

-- ============================================
-- Step 3: Verify All Rows Have clerk_org_id Populated
-- ============================================

DO $$
DECLARE
    tables_to_check TEXT[] := ARRAY[
        'agents', 'calls', 'voices', 'knowledge_bases', 
        'tools', 'contacts', 'contact_folders', 'campaigns', 'webhook_endpoints'
    ];
    table_name TEXT;
    null_count INTEGER;
    total_count INTEGER;
BEGIN
    FOREACH table_name IN ARRAY tables_to_check
    LOOP
        EXECUTE format('SELECT COUNT(*) FROM %I WHERE clerk_org_id IS NULL', table_name) INTO null_count;
        EXECUTE format('SELECT COUNT(*) FROM %I', table_name) INTO total_count;
        
        IF null_count > 0 THEN
            RAISE WARNING 'Table % has % rows with NULL clerk_org_id (out of % total rows)', table_name, null_count, total_count;
        ELSE
            RAISE NOTICE '✓ Table %: All % rows have clerk_org_id populated', table_name, total_count;
        END IF;
    END LOOP;
END $$;

-- ============================================
-- Step 4: Verify Indexes on clerk_org_id Exist
-- ============================================

DO $$
DECLARE
    expected_indexes TEXT[] := ARRAY[
        'idx_agents_clerk_org_id',
        'idx_calls_clerk_org_id',
        'idx_voices_clerk_org_id',
        'idx_knowledge_bases_clerk_org_id',
        'idx_tools_clerk_org_id',
        'idx_contacts_clerk_org_id',
        'idx_contact_folders_clerk_org_id',
        'idx_campaigns_clerk_org_id',
        'idx_webhook_endpoints_clerk_org_id'
    ];
    index_name TEXT;
    index_exists BOOLEAN;
BEGIN
    FOREACH index_name IN ARRAY expected_indexes
    LOOP
        SELECT EXISTS (
            SELECT 1 FROM pg_indexes 
            WHERE schemaname = 'public' 
            AND indexname = index_name
        ) INTO index_exists;
        
        IF NOT index_exists THEN
            RAISE WARNING 'Index % missing! Consider creating it for performance.', index_name;
        ELSE
            RAISE NOTICE '✓ Index % exists', index_name;
        END IF;
    END LOOP;
END $$;

-- ============================================
-- Step 5: Verify Foreign Key Constraints Are Removed
-- ============================================

DO $$
DECLARE
    tables_to_check TEXT[] := ARRAY[
        'agents', 'calls', 'voices', 'knowledge_bases', 
        'tools', 'contacts', 'contact_folders', 'campaigns', 'webhook_endpoints'
    ];
    table_name TEXT;
    constraint_exists BOOLEAN;
    constraint_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY tables_to_check
    LOOP
        SELECT EXISTS (
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu 
                ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_schema = 'public'
            AND tc.table_name = table_name
            AND tc.constraint_type = 'FOREIGN KEY'
            AND kcu.column_name = 'client_id'
        ) INTO constraint_exists;
        
        IF constraint_exists THEN
            SELECT constraint_name INTO constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu 
                ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_schema = 'public'
            AND tc.table_name = table_name
            AND tc.constraint_type = 'FOREIGN KEY'
            AND kcu.column_name = 'client_id'
            LIMIT 1;
            
            RAISE EXCEPTION 'Table % still has foreign key constraint % on client_id!', table_name, constraint_name;
        ELSE
            RAISE NOTICE '✓ Table %: No foreign key constraints on client_id', table_name;
        END IF;
    END LOOP;
END $$;

-- ============================================
-- Step 6: Verify Billing Tables Still Have client_id
-- ============================================

DO $$
DECLARE
    billing_tables TEXT[] := ARRAY[
        'clients', 'users', 'api_keys', 'credit_transactions', 
        'audit_log', 'application_logs', 'idempotency_keys'
    ];
    table_name TEXT;
    has_client_id BOOLEAN;
BEGIN
    FOREACH table_name IN ARRAY billing_tables
    LOOP
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = table_name 
            AND column_name = 'client_id'
        ) INTO has_client_id;
        
        IF NOT has_client_id THEN
            RAISE WARNING 'Billing table % missing client_id column! This may be intentional.', table_name;
        ELSE
            RAISE NOTICE '✓ Billing table %: client_id column exists (as expected)', table_name;
        END IF;
    END LOOP;
END $$;

-- ============================================
-- Summary
-- ============================================
SELECT 
    'Verification Complete!' as status,
    'All main app tables should have client_id removed and clerk_org_id populated' as note;
