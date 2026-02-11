.PHONY: help setup-creds validate-creds build up down logs diagram clean

help:
	@echo "Tagging API - Available Commands"
	@echo "=================================="
	@echo "Credenciais:"
	@echo "  make setup-creds      - Gerar/atualizar key.json com gcloud"
	@echo "  make validate-creds   - Validar credenciais existentes"
	@echo ""
	@echo "Docker:"
	@echo "  make build           - Build Docker image"
	@echo "  make up              - Start containers (requer key.json)"
	@echo "  make down            - Stop containers"
	@echo "  make logs            - Show container logs"
	@echo ""
	@echo "Arquitetura:"
	@echo "  make diagram         - Generate architecture diagram"
	@echo "  make clean           - Clean generated files"

# === Credenciais ===
setup-creds:
	@echo "⚙️  Configurando credenciais..."
	@chmod +x deployment/setup-credentials.sh
	@./deployment/setup-credentials.sh

validate-creds:
	@echo "🔍 Validando credenciais..."
	@chmod +x deployment/validate-credentials.sh
	@./deployment/validate-credentials.sh

# === Docker ===
build:
	@echo "🐳 Building Docker image..."
	docker compose build

up: validate-creds
	@echo "🚀 Starting containers..."
	docker compose up -d
	@echo "✓ API disponível em http://localhost:8080"

down:
	@echo "🛑 Stopping containers..."
	docker compose down

logs:
	docker compose logs -f tagging-api

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
