#!/bin/bash
# Script para criar o Artifact Registry (executar uma vez)

set -e

PROJECT_ID="tagging-api-481123"
REGION="southamerica-east1"
REPOSITORY="tagging-api-repo"

echo "📦 Configurando Artifact Registry..."
echo ""

# 1. Habilitar API
echo "🔧 Habilitando Artifact Registry API..."
gcloud services enable artifactregistry.googleapis.com --project=${PROJECT_ID}

# 2. Criar repositório Docker
echo "📦 Criando repositório Docker..."
gcloud artifacts repositories create ${REPOSITORY} \
    --repository-format=docker \
    --location=${REGION} \
    --description="Docker images para tagging-api" \
    --project=${PROJECT_ID} 2>/dev/null || echo "   ✅ Repositório já existe"

# 3. Verificar
echo ""
echo "✅ Artifact Registry configurado!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📋 Informações do Repositório:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Projeto: ${PROJECT_ID}"
echo "Região: ${REGION}"
echo "Repositório: ${REPOSITORY}"
echo "URL: ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}"
echo ""
echo "🔗 Visualizar no Console:"
echo "   https://console.cloud.google.com/artifacts/docker/${PROJECT_ID}/${REGION}/${REPOSITORY}"
echo ""
