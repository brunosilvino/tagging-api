#!/bin/bash
# Script para gerar/atualizar key.json automaticamente
# Usa a conta de serviço 'developer' existente no projeto GCP
# Uso: ./deployment/setup-credentials.sh

set -e

PROJECT_ID="tagging-api-481123"
SERVICE_ACCOUNT="developer"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT}@${PROJECT_ID}.iam.gserviceaccount.com"
KEY_PATH="./key.json"

echo "🔐 Setup de Credenciais GCP para Desenvolvimento Local"
echo "========================================================"
echo ""

# Verificar se gcloud está instalado
if ! command -v gcloud &> /dev/null; then
    echo "❌ gcloud CLI não encontrado. Instale com:"
    echo "   brew install --cask google-cloud-sdk"
    exit 1
fi

# Verificar autenticação
echo "✓ Verificando autenticação com gcloud..."
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" &> /dev/null; then
    echo "❌ Nenhuma conta autenticada. Execute:"
    echo "   gcloud auth login"
    exit 1
fi

CURRENT_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)")
echo "  Autenticado como: $CURRENT_ACCOUNT"
echo ""

# Verificar projeto
echo "✓ Verificando projeto GCP..."
gcloud config set project "$PROJECT_ID" --quiet
echo "  Projeto: $PROJECT_ID"
echo ""

# Verificar se conta de serviço existe
echo "✓ Verificando conta de serviço: $SERVICE_ACCOUNT_EMAIL"
if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" \
    --project="$PROJECT_ID" &> /dev/null; then
    echo "❌ Conta de serviço '$SERVICE_ACCOUNT' não existe!"
    echo "   Criando..."
    gcloud iam service-accounts create "$SERVICE_ACCOUNT" \
        --display-name="Tagging API Development" \
        --project="$PROJECT_ID"
    echo "✓ Conta de serviço criada"
fi
echo ""

# Gerar nova chave JSON (substitui a antiga)
echo "🔑 Gerando chave JSON..."
if [ -f "$KEY_PATH" ]; then
    echo "  Removendo chave anterior..."
    rm -f "$KEY_PATH"
fi

gcloud iam service-accounts keys create "$KEY_PATH" \
    --iam-account="$SERVICE_ACCOUNT_EMAIL" \
    --project="$PROJECT_ID"

echo "✓ Chave gerada em: $KEY_PATH"
echo ""

# Conceder permissões necessárias
echo "✓ Configurando permissões..."

ROLES=(
    "roles/bigquery.dataEditor"
    "roles/datastore.user"
)

for ROLE in "${ROLES[@]}"; do
    if gcloud projects get-iam-policy "$PROJECT_ID" \
        --flatten="bindings[].members" \
        --filter="bindings.role:$ROLE AND bindings.members:serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
        --format="value(bindings.role)" 2>/dev/null | grep -q "$ROLE"; then
        echo "  ✓ $ROLE (já configurado)"
    else
        echo "  ➕ Adicionando $ROLE..."
        gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
            --role="$ROLE" \
            --quiet
        echo "  ✓ $ROLE (adicionado)"
    fi
done
echo ""

# Validar chave
echo "✓ Validando chave..."
if grep -q "\"type\": \"service_account\"" "$KEY_PATH"; then
    echo "  ✓ Formato válido"
else
    echo "  ❌ Formato inválido!"
    exit 1
fi
echo ""

echo "✅ Setup concluído com sucesso!"
echo ""
echo "📝 Próximos passos:"
echo "   1. Adicione ao seu .env (se usar):"
echo "      export GOOGLE_APPLICATION_CREDENTIALS='./key.json'"
echo ""
echo "   2. Inicie o container:"
echo "      docker compose up -d"
echo ""
echo "   3. Verifique se está funcionando:"
echo "      curl http://localhost:8080/"
echo ""
