#!/bin/bash
# Script para fazer deploy manual do tagging-api no Cloud Run

set -e

: "${API_KEY:?Defina API_KEY antes do deploy executando \'API_KEY=sua-chave ./deployment/deploy.sh\'}"

PROJECT_ID="tagging-api-481123"
REGISTRY="southamerica-east1-docker.pkg.dev"
REPOSITORY="tagging-api-repo"
IMAGE_NAME="tagging-api"
REGION="southamerica-east1"
SERVICE_NAME="tagging-api"
REDIS_INSTANCE="${REDIS_INSTANCE:-tagging-redis}"
VPC_CONNECTOR="${VPC_CONNECTOR:-tagging-api-connector}"
TAG="${1:-latest}"  # Padrão: latest; ou passe um git commit hash

echo "🚀 Iniciando deploy da tagging-api..."
echo "   Projeto: $PROJECT_ID"
echo "   Região: $REGION"
echo "   Redis: $REDIS_INSTANCE"
echo "   VPC Connector: $VPC_CONNECTOR"
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
REDIS_HOST=$(gcloud redis instances describe ${REDIS_INSTANCE} \
    --project ${PROJECT_ID} \
    --region ${REGION} \
    --format='value(host)')

: "${REDIS_HOST:?Não foi possível obter o IP do Memorystore ${REDIS_INSTANCE}}"

gcloud run deploy ${SERVICE_NAME} \
    --image ${REGISTRY}/${PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:${TAG} \
    --region ${REGION} \
    --platform managed \
    --memory 512Mi \
    --cpu 1 \
    --timeout 300 \
    --vpc-connector=${VPC_CONNECTOR} \
    --vpc-egress=private-ranges-only \
    --set-env-vars GOOGLE_CLOUD_PROJECT=${PROJECT_ID},FLASK_ENV=production,DEDUP_TTL=2.0,DEDUP_MAXSIZE=1000,API_KEY=${API_KEY},REDIS_HOST=${REDIS_HOST},REDIS_PORT=6379,REDIS_DB=0 \
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
echo "  curl -X POST $URL/loadmaps -H 'Content-Type: application/json' -d '[\"00001\",\"00002\"]'"
