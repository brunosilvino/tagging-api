#!/bin/bash
# Configura o Memorystore for Redis e o VPC Connector usados pelo Cloud Run.

set -e

PROJECT_ID="${PROJECT_ID:-tagging-api-481123}"
REGION="${REGION:-southamerica-east1}"
REDIS_INSTANCE="${REDIS_INSTANCE:-tagging-redis}"
VPC_CONNECTOR="${VPC_CONNECTOR:-tagging-api-connector}"
NETWORK="${NETWORK:-default}"
CONNECTOR_RANGE="${CONNECTOR_RANGE:-10.8.0.0/28}"

echo "🧱 Configurando Redis para o tagging-api..."
echo "   Projeto: ${PROJECT_ID}"
echo "   Região: ${REGION}"
echo "   Redis: ${REDIS_INSTANCE}"
echo "   VPC Connector: ${VPC_CONNECTOR}"
echo ""

echo "🔧 Habilitando APIs do Redis e VPC Access..."
gcloud services enable \
    redis.googleapis.com \
    vpcaccess.googleapis.com \
    --project="${PROJECT_ID}"

if gcloud redis instances describe "${REDIS_INSTANCE}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    >/dev/null 2>&1; then
    echo "✅ Instância Redis já existe: ${REDIS_INSTANCE}"
else
    echo "🧠 Criando instância Memorystore..."
    gcloud redis instances create "${REDIS_INSTANCE}" \
        --project="${PROJECT_ID}" \
        --region="${REGION}" \
        --tier=basic \
        --size=1 \
        --redis-version=redis_7_2 \
        --network="projects/${PROJECT_ID}/global/networks/${NETWORK}"
fi

if gcloud compute networks vpc-access connectors describe "${VPC_CONNECTOR}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    >/dev/null 2>&1; then
    echo "✅ VPC Connector já existe: ${VPC_CONNECTOR}"
else
    echo "🔌 Criando VPC Connector..."
    gcloud compute networks vpc-access connectors create "${VPC_CONNECTOR}" \
        --project="${PROJECT_ID}" \
        --region="${REGION}" \
        --network="${NETWORK}" \
        --range="${CONNECTOR_RANGE}"
fi

REDIS_HOST=$(gcloud redis instances describe "${REDIS_INSTANCE}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --format='value(host)')

echo ""
echo "✅ Redis e VPC Connector configurados!"
echo ""
echo "Redis: ${REDIS_INSTANCE}"
echo "Host: ${REDIS_HOST}"
echo "Porta: 6379"
echo "Rede: ${NETWORK}"
echo "VPC Connector: ${VPC_CONNECTOR}"
