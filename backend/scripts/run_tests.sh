#!/bin/bash
# Run PAYGO Middleware Tests
#
# This script runs the test suite with various options.
#
# Usage:
#   ./scripts/run_tests.sh           # Run all tests
#   ./scripts/run_tests.sh unit      # Run unit tests only
#   ./scripts/run_tests.sh int       # Run integration tests only
#   ./scripts/run_tests.sh cov       # Run with coverage
#   ./scripts/run_tests.sh fast      # Run fast (no coverage, parallel)

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== PAYGO Middleware Test Runner ===${NC}"

# Load test environment
if [ -f .env.test ]; then
    export $(cat .env.test | grep -v '^#' | xargs)
    echo -e "${GREEN}Loaded test environment from .env.test${NC}"
elif [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
    echo -e "${YELLOW}Using .env (consider creating .env.test for testing)${NC}"
fi

# Set test environment
export ENVIRONMENT=testing

# Determine test mode
MODE=${1:-all}

case $MODE in
    unit)
        echo -e "\n${GREEN}Running unit tests...${NC}"
        pytest tests/unit/ -v
        ;;

    int|integration)
        echo -e "\n${GREEN}Running integration tests...${NC}"
        pytest tests/integration/ -v
        ;;

    cov|coverage)
        echo -e "\n${GREEN}Running tests with coverage...${NC}"
        pytest --cov=app --cov-report=html --cov-report=term-missing -v

        echo -e "\n${GREEN}Coverage report generated: htmlcov/index.html${NC}"
        ;;

    fast)
        echo -e "\n${GREEN}Running tests in fast mode...${NC}"
        pytest -x -q --tb=short
        ;;

    watch)
        echo -e "\n${GREEN}Running tests in watch mode...${NC}"
        echo -e "${YELLOW}Note: Requires pytest-watch (pip install pytest-watch)${NC}"
        ptw -- -v --tb=short
        ;;

    ci)
        echo -e "\n${GREEN}Running CI test suite...${NC}"
        pytest --cov=app --cov-report=xml --cov-fail-under=70 -v --tb=short

        if [ $? -eq 0 ]; then
            echo -e "${GREEN}CI tests passed!${NC}"
        else
            echo -e "${RED}CI tests failed!${NC}"
            exit 1
        fi
        ;;

    all)
        echo -e "\n${GREEN}Running all tests...${NC}"
        pytest -v
        ;;

    *)
        echo -e "${YELLOW}Usage: $0 [unit|int|cov|fast|watch|ci|all]${NC}"
        echo ""
        echo "Modes:"
        echo "  unit    - Run unit tests only"
        echo "  int     - Run integration tests only"
        echo "  cov     - Run with coverage report"
        echo "  fast    - Fast run (stop on first failure)"
        echo "  watch   - Watch mode (requires pytest-watch)"
        echo "  ci      - CI mode with coverage threshold"
        echo "  all     - Run all tests (default)"
        exit 1
        ;;
esac

# Print summary
if [ $? -eq 0 ]; then
    echo -e "\n${GREEN}=== Tests completed successfully ===${NC}"
else
    echo -e "\n${RED}=== Some tests failed ===${NC}"
    exit 1
fi
