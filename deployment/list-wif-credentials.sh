#!/bin/bash
# Script para listar recursos WIF existentes (não precisa de permissões de escrita)

set -e

PROJECT_ID="tagging-api-481123"
REGION="southamerica-east1"
WIF_POOL_ID="github-pool"
WIF_PROVIDER_ID="github-provider"
SERVICE_ACCOUNT="github-actions@${PROJECT_ID}.iam.gserviceaccount.com"

echo "📋 Verificando recursos WIF existentes..."
echo ""

# WIF Provider
WIF_PROVIDER="projects/${PROJECT_ID}/locations/global/workloadIdentityPools/${WIF_POOL_ID}/providers/${WIF_PROVIDER_ID}"

echo "✅ Recursos identificados!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📝 ADICIONE ESTES VALORES AOS GITHUB SECRETS:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Nome do Secret: WIF_PROVIDER"
echo "Valor:"
echo "${WIF_PROVIDER}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Nome do Secret: WIF_SERVICE_ACCOUNT"
echo "Valor:"
echo "${SERVICE_ACCOUNT}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Nome do Secret: ADMIN_KEY"
echo "Valor (gere um novo):"
openssl rand -hex 32 2>/dev/null || echo "GERE_SEU_PROPRIO_SECRET_AQUI"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🔗 Acesse e adicione os secrets:"
echo "   https://github.com/brunosilvino/tagging-api/settings/secrets/actions"
echo ""
echo "⚠️  IMPORTANTE: Se o WIF pool estiver em 'global' em vez de 'southamerica-east1',"
echo "    o valor correto do WIF_PROVIDER é:"
echo "    projects/${PROJECT_ID}/locations/global/workloadIdentityPools/${WIF_POOL_ID}/providers/${WIF_PROVIDER_ID}"
echo ""
