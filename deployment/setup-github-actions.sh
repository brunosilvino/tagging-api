#!/bin/bash
# Script para configurar o GitHub Actions com Workload Identity Federation
# Execute este script uma vez para preparar a autenticação

set -e

PROJECT_ID="tagging-api-481123"
SERVICE_ACCOUNT="github-actions@${PROJECT_ID}.iam.gserviceaccount.com"
GITHUB_REPO_OWNER="brunosilvino"
GITHUB_REPO_NAME="tagging-api"
WIF_POOL_ID="github-pool"
WIF_PROVIDER_ID="github-provider"
REGION="southamerica-east1"

echo "🔐 Configurando Workload Identity Federation para GitHub Actions..."

# 1. Habilitar APIs necessárias
echo "📦 Habilitando Google Cloud APIs..."
gcloud services enable iam.googleapis.com \
    iamcredentials.googleapis.com \
    cloudresourcemanager.googleapis.com \
    sts.googleapis.com

# 2. Criar Service Account se não existir
echo "👤 Criando Service Account..."
gcloud iam service-accounts create github-actions \
    --display-name="GitHub Actions" \
    --project=${PROJECT_ID} || echo "   Service Account já existe."

# 3. Conceder permissões necessárias
echo "🔑 Concedendo permissões..."
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/run.admin"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/artifactregistry.admin"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/iam.serviceAccountUser"

# 4. Criar Workload Identity Pool e Provider
echo "🌐 Criando Workload Identity Pool..."
gcloud iam workload-identity-pools create ${WIF_POOL_ID} \
    --project=${PROJECT_ID} \
    --location=${REGION} \
    --display-name="GitHub Pool" || echo "   Pool já existe."

echo "🔗 Criando Workload Identity Provider..."
gcloud iam workload-identity-pools providers create-oidc ${WIF_PROVIDER_ID} \
    --project=${PROJECT_ID} \
    --location=${REGION} \
    --workload-identity-pool=${WIF_POOL_ID} \
    --display-name="GitHub Provider" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-condition="assertion.repository_owner == '${GITHUB_REPO_OWNER}'" || echo "   Provider já existe."

# 5. Obter WIF Provider Resource Name
WIF_PROVIDER=$(gcloud iam workload-identity-pools providers describe ${WIF_PROVIDER_ID} \
    --project=${PROJECT_ID} \
    --location=${REGION} \
    --workload-identity-pool=${WIF_POOL_ID} \
    --format="value(name)")

# 6. Vincular GitHub ao Service Account
echo "⛓️  Vinculando GitHub ao Service Account..."
gcloud iam service-accounts add-iam-policy-binding ${SERVICE_ACCOUNT} \
    --project=${PROJECT_ID} \
    --role="roles/iam.workloadIdentityUser" \
    --principalSet=//iam.googleapis.com/projects/${PROJECT_ID}/locations/${REGION}/workloadIdentityPools/${WIF_POOL_ID}/providers/${WIF_PROVIDER_ID}/attributes.repository/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}

# 7. Exibir valores para configurar no GitHub
echo ""
echo "✅ Configuração concluída!"
echo ""
echo "📋 Adicione estes Secrets no GitHub (Settings → Secrets and variables → Actions):"
echo ""
echo "WIF_PROVIDER:"
echo "  ${WIF_PROVIDER}"
echo ""
echo "WIF_SERVICE_ACCOUNT:"
echo "  ${SERVICE_ACCOUNT}"
echo ""
echo "ADMIN_KEY (opcional - gere uma senha segura):"
echo "  $(openssl rand -hex 32)"
echo ""
echo "💡 Próximos passos:"
echo "  1. Vá para: https://github.com/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/settings/secrets/actions"
echo "  2. Crie 3 secrets: WIF_PROVIDER, WIF_SERVICE_ACCOUNT, ADMIN_KEY"
echo "  3. Faça um push para main e acompanhe o deploy em: https://github.com/${GITHUB_REPO_OWNER}/${GITHUB_REPO_NAME}/actions"
