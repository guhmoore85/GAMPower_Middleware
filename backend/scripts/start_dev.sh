#!/bin/bash
# Start PAYGO Middleware Development Server
#
# This script starts the development environment with all dependencies.
#
# Usage:
#   ./scripts/start_dev.sh          # Start with Docker Compose
#   ./scripts/start_dev.sh local    # Start locally (requires Postgres/Redis)
#   ./scripts/start_dev.sh docker   # Start only supporting services

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== PAYGO Middleware Development Server ===${NC}"

# Check for .env file
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo -e "${YELLOW}Creating .env from .env.example...${NC}"
        cp .env.example .env
        echo -e "${GREEN}.env file created. Please review and update settings.${NC}"
    else
        echo -e "${RED}Error: .env file not found and no .env.example to copy.${NC}"
        exit 1
    fi
fi

# Load environment
export $(cat .env | grep -v '^#' | xargs)

MODE=${1:-docker-compose}

case $MODE in
    docker|services)
        echo -e "\n${GREEN}Starting supporting services (PostgreSQL, Redis)...${NC}"
        docker-compose up -d postgres redis

        echo -e "\n${GREEN}Waiting for services to be ready...${NC}"
        sleep 3

        # Check PostgreSQL
        until docker-compose exec -T postgres pg_isready -q; do
            echo "Waiting for PostgreSQL..."
            sleep 1
        done
        echo -e "${GREEN}PostgreSQL is ready${NC}"

        # Check Redis
        until docker-compose exec -T redis redis-cli ping | grep -q PONG; do
            echo "Waiting for Redis..."
            sleep 1
        done
        echo -e "${GREEN}Redis is ready${NC}"

        echo -e "\n${GREEN}Services started. Run the app locally:${NC}"
        echo -e "${YELLOW}  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000${NC}"
        ;;

    local)
        echo -e "\n${GREEN}Starting local development server...${NC}"

        # Check PostgreSQL connection
        echo "Checking PostgreSQL connection..."
        if ! pg_isready -h localhost -p 5432 -q 2>/dev/null; then
            echo -e "${RED}PostgreSQL not accessible at localhost:5432${NC}"
            echo "Start PostgreSQL or use: ./scripts/start_dev.sh docker"
            exit 1
        fi
        echo -e "${GREEN}PostgreSQL is accessible${NC}"

        # Check Redis connection
        echo "Checking Redis connection..."
        if ! redis-cli -h localhost ping 2>/dev/null | grep -q PONG; then
            echo -e "${RED}Redis not accessible at localhost:6379${NC}"
            echo "Start Redis or use: ./scripts/start_dev.sh docker"
            exit 1
        fi
        echo -e "${GREEN}Redis is accessible${NC}"

        # Run migrations
        echo -e "\n${GREEN}Running migrations...${NC}"
        alembic upgrade head

        # Start server
        echo -e "\n${GREEN}Starting uvicorn...${NC}"
        echo -e "${YELLOW}API Docs: http://localhost:8000/docs${NC}"
        echo -e "${YELLOW}Health:   http://localhost:8000/health${NC}"
        echo ""

        uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
        ;;

    docker-compose|full)
        echo -e "\n${GREEN}Starting full development environment with Docker Compose...${NC}"

        # Build and start all services
        docker-compose up --build -d

        echo -e "\n${GREEN}Waiting for services to be ready...${NC}"
        sleep 5

        # Run migrations
        echo -e "\n${GREEN}Running migrations...${NC}"
        docker-compose exec -T app alembic upgrade head

        echo -e "\n${GREEN}=== Development environment is ready ===${NC}"
        echo -e "${YELLOW}API:     http://localhost:8000${NC}"
        echo -e "${YELLOW}Docs:    http://localhost:8000/docs${NC}"
        echo -e "${YELLOW}Health:  http://localhost:8000/health${NC}"
        echo -e "${YELLOW}pgAdmin: http://localhost:5050 (admin@paygo.local / admin)${NC}"
        echo ""
        echo -e "View logs: ${BLUE}docker-compose logs -f app${NC}"
        echo -e "Stop:      ${BLUE}docker-compose down${NC}"
        ;;

    stop)
        echo -e "\n${GREEN}Stopping all services...${NC}"
        docker-compose down
        echo -e "${GREEN}All services stopped${NC}"
        ;;

    logs)
        echo -e "\n${GREEN}Showing application logs...${NC}"
        docker-compose logs -f app
        ;;

    shell)
        echo -e "\n${GREEN}Opening shell in app container...${NC}"
        docker-compose exec app /bin/bash
        ;;

    *)
        echo -e "${YELLOW}Usage: $0 [docker|local|docker-compose|stop|logs|shell]${NC}"
        echo ""
        echo "Modes:"
        echo "  docker-compose  - Start full environment with Docker (default)"
        echo "  docker/services - Start only PostgreSQL and Redis in Docker"
        echo "  local           - Start app locally (requires local Postgres/Redis)"
        echo "  stop            - Stop all Docker services"
        echo "  logs            - Show application logs"
        echo "  shell           - Open shell in app container"
        exit 1
        ;;
esac
