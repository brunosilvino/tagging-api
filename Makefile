.PHONY: help build api db pgadmin up down logs query clean

help:
	@echo "Tagging API - Available Commands"
	@echo "=================================="
	@echo "Banco:"
	@echo "  make db              - Subir o PostgreSQL local"
	@echo "  make pgadmin         - Abrir o pgAdmin em http://localhost:5050"
	@echo "  make query           - Open PostgreSQL query prompt"
	@echo ""
	@echo "Docker:"
	@echo "  make build           - Build Docker image"
	@echo "  make api             - Rebuild and restart only the API"
	@echo "  make up              - Start API e PostgreSQL"
	@echo "  make down            - Stop containers and remove volumes"
	@echo "  make logs            - Show container logs"
	@echo ""
	@echo "Arquitetura:"
	@echo "  make diagram         - Generate architecture diagram"
	@echo "  make clean           - Clean generated files"

# === Docker ===
build:
	@echo "🐳 Building all Docker images..."
	docker compose build

api:
	@echo "🔄 Rebuilding and restarting only the API..."
	docker compose up -d --build --no-deps tagging-api

db:
	@echo "🔄 Recreating PostgreSQL and reloading CSVs..."
	docker compose stop postgres
	docker compose rm -f postgres
	volume=$$(docker volume ls -q --filter label=com.docker.compose.volume=postgres_data); \
	if [ -n "$$volume" ]; then docker volume rm $$volume; fi
	docker compose up -d postgres
	@echo "⏳ Waiting for PostgreSQL initialization..."
	@attempt=0; \
	until docker compose exec -T postgres psql -U tagging -d tagging -c "SELECT 1 FROM map_taxonomy LIMIT 1" >/dev/null 2>&1; do \
		attempt=$$((attempt + 1)); \
		if [ $$attempt -ge 60 ]; then echo "PostgreSQL initialization timed out."; exit 1; fi; \
	done
	@echo "✅ PostgreSQL ready with imported data."

pgadmin:
	@echo "🌐 pgAdmin disponível em http://localhost:5050"
	docker compose up -d pgadmin

up:
	@echo "🚀 Starting containers..."
	docker compose up -d
	@echo "✓ API disponível em http://localhost:8080"

down:
	@echo "🛑 Stopping containers and removing volumes..."
	docker compose down -v

logs:
	docker compose logs -f tagging-api

query:
ifdef QUERY
	docker compose exec -T postgres psql -U tagging -d tagging -c "$(QUERY)"
else
	docker compose exec postgres psql -U tagging -d tagging
endif

# === Arquitetura ===
diagram:
	@echo "🎨 Generating architecture diagram..."
	docker compose run --rm tagging-api python /app/generate_architecture_diagram.py
	@echo "✅ Diagram generated successfully!"

# === Limpeza ===
clean:
	@echo "🧹 Cleaning generated files..."
	rm -f architecture_diagram.png
	rm -f architecture_diagram.html
	rm -f architecture_diagram_*
	rm -rf diagrams_output/
	rm -f *.dot *.svg
	@echo "✅ Cleanup complete"

.DEFAULT_GOAL := help
