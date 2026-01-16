#!/bin/bash
# Database Migration Runner
# Safely runs all pending migrations

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Running database migrations...${NC}"

# Check if .env file exists
if [ ! -f "../../.env" ]; then
    echo -e "${RED}ERROR: .env file not found in project root${NC}"
    exit 1
fi

# Load environment variables
source ../../.env

# Check if Supabase URL and key are set
if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_SERVICE_KEY" ]; then
    echo -e "${RED}ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set${NC}"
    exit 1
fi

# Get database connection string from Supabase
# You can also use psql directly if you have the connection string
echo -e "${YELLOW}Note: This script assumes you're using Supabase SQL Editor${NC}"
echo -e "${YELLOW}For direct psql, uncomment and configure the psql command below${NC}"

# Option 1: Using Supabase SQL Editor (recommended)
echo -e "${GREEN}Migration files to run:${NC}"
ls -1 *.sql | while read file; do
    echo "  - $file"
done

echo -e "${YELLOW}Please run these migrations in Supabase SQL Editor:${NC}"
echo "1. Go to your Supabase Dashboard"
echo "2. Navigate to SQL Editor"
echo "3. Run each migration file in order:"
ls -1 *.sql | nl

# Option 2: Using psql (uncomment and configure if needed)
# PGHOST=$(echo $SUPABASE_URL | sed 's|https://||' | sed 's|\.supabase\.co.*||')
# PGPASSWORD=$SUPABASE_SERVICE_KEY
# 
# for migration in *.sql; do
#     echo "Running $migration..."
#     psql -h $PGHOST -U postgres -d postgres -f "$migration"
# done

echo -e "${GREEN}✅ Migration instructions provided${NC}"

