#!/bin/bash
# Initialize PAYGO Middleware Database
#
# This script:
# 1. Creates the database if it doesn't exist
# 2. Runs all Alembic migrations
# 3. Optionally seeds initial data
#
# Usage: ./scripts/init_db.sh [--seed]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== PAYGO Middleware Database Initialization ===${NC}"

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
    echo -e "${GREEN}Loaded environment from .env${NC}"
else
    echo -e "${YELLOW}Warning: .env file not found. Using environment variables.${NC}"
fi

# Parse DATABASE_URL to get connection details
# Format: postgresql+asyncpg://user:password@host:port/database
if [ -z "$DATABASE_URL" ]; then
    echo -e "${RED}Error: DATABASE_URL not set${NC}"
    exit 1
fi

# Extract database name from URL
DB_NAME=$(echo $DATABASE_URL | sed -n 's/.*\/\([^?]*\).*/\1/p')
DB_HOST=$(echo $DATABASE_URL | sed -n 's/.*@\([^:\/]*\).*/\1/p')
DB_PORT=$(echo $DATABASE_URL | sed -n 's/.*:\([0-9]*\)\/.*/\1/p')
DB_USER=$(echo $DATABASE_URL | sed -n 's/.*:\/\/\([^:]*\):.*/\1/p')

# Default port if not specified
DB_PORT=${DB_PORT:-5432}

echo -e "${GREEN}Database: ${DB_NAME}${NC}"
echo -e "${GREEN}Host: ${DB_HOST}:${DB_PORT}${NC}"

# Check if PostgreSQL is accessible
echo -e "\n${GREEN}Checking PostgreSQL connection...${NC}"
if ! pg_isready -h "$DB_HOST" -p "$DB_PORT" -q; then
    echo -e "${RED}Error: Cannot connect to PostgreSQL at ${DB_HOST}:${DB_PORT}${NC}"
    echo "Make sure PostgreSQL is running and accessible."
    exit 1
fi
echo -e "${GREEN}PostgreSQL is accessible${NC}"

# Create database if it doesn't exist
echo -e "\n${GREEN}Checking if database exists...${NC}"
if psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -lqt | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
    echo -e "${YELLOW}Database ${DB_NAME} already exists${NC}"
else
    echo -e "${GREEN}Creating database ${DB_NAME}...${NC}"
    createdb -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME"
    echo -e "${GREEN}Database created${NC}"
fi

# Run Alembic migrations
echo -e "\n${GREEN}Running database migrations...${NC}"
alembic upgrade head

if [ $? -eq 0 ]; then
    echo -e "${GREEN}Migrations completed successfully${NC}"
else
    echo -e "${RED}Error running migrations${NC}"
    exit 1
fi

# Seed data if --seed flag provided
if [ "$1" = "--seed" ]; then
    echo -e "\n${GREEN}Seeding initial data...${NC}"
    python scripts/seed_data.py

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}Data seeded successfully${NC}"
    else
        echo -e "${RED}Error seeding data${NC}"
        exit 1
    fi
fi

# Verify tables were created
echo -e "\n${GREEN}Verifying database schema...${NC}"
TABLES=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';")
TABLES=$(echo $TABLES | xargs)  # Trim whitespace

if [ "$TABLES" -gt 0 ]; then
    echo -e "${GREEN}Found ${TABLES} tables in database${NC}"
else
    echo -e "${RED}Warning: No tables found. Migrations may have failed.${NC}"
fi

echo -e "\n${GREEN}=== Database initialization complete ===${NC}"
echo -e "You can now start the application with: ${YELLOW}uvicorn app.main:app --reload${NC}"
