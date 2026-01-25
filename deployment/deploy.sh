#!/bin/bash
# Script para fazer deploy manual do tagging-api no Cloud Run

set -e

PROJECT_ID="tagging-api-481123"
REGISTRY="us-central1-docker.pkg.dev"
REPOSITORY="tagging-api-repo"
IMAGE_NAME="tagging-api"
REGION="southamerica-east1"
SERVICE_NAME="tagging-api"
TAG="${1:-latest}"  # Padrão: latest; ou passe um git commit hash

echo "🚀 Iniciando deploy do tagging-api..."
echo "   Projeto: $PROJECT_ID"
echo "   Região: $REGION"
echo "   Tag: $TAG"
echo ""

# 1. Autenticar
echo "🔐 Autenticando com Google Cloud..."
gcloud auth configure-docker ${REGISTRY}

# 2. Build
echo "🔨 Fazendo build da imagem Docker..."
docker build \
    -t ${REGISTRY}/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${TAG} \
    -t ${REGISTRY}/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:latest \
    ./api

# 3. Push
echo "📤 Fazendo push para Artifact Registry..."
docker push ${REGISTRY}/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${TAG}
docker push ${REGISTRY}/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:latest

# 4. Deploy
echo "☁️  Fazendo deploy no Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
    --image ${REGISTRY}/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${TAG} \
    --region ${REGION} \
    --platform managed \
    --memory 512Mi \
    --cpu 1 \
    --timeout 300 \
    --set-env-vars GOOGLE_CLOUD_PROJECT=${PROJECT_ID},DEDUP_TTL=2.0,DEDUP_MAXSIZE=1000 \
    --service-account developer@${PROJECT_ID}.iam.gserviceaccount.com \
    --allow-unauthenticated

# 5. Obter URL
URL=$(gcloud run services describe ${SERVICE_NAME} \
    --region ${REGION} \
    --format='value(status.url)')

echo ""
echo "✅ Deploy concluído com sucesso!"
echo "🌐 URL: $URL"
echo ""
echo "Teste a API:"
echo "  curl $URL/"
echo "  curl -X POST $URL/loadmap -H 'Content-Type: application/json' -d '{\"map_id\":\"00001\"}'"
