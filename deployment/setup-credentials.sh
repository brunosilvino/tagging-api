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
CURRENT_ACCOUNT=$(gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>/dev/null | head -n 1)
if [ -z "$CURRENT_ACCOUNT" ]; then
    echo "❌ Nenhuma conta autenticada. Execute:"
    echo "   gcloud auth login"
    exit 1
fi

if [ -e "$KEY_PATH" ] && [ ! -f "$KEY_PATH" ]; then
    echo "❌ O caminho $KEY_PATH existe, mas não é um arquivo."
    echo "   Remova ou renomeie esse diretório e execute o comando novamente."
    exit 1
fi

echo "  Autenticado como: $CURRENT_ACCOUNT"
echo ""

if [ "$CURRENT_ACCOUNT" = "$SERVICE_ACCOUNT_EMAIL" ]; then
    echo "❌ O gcloud está autenticado como a própria service account da aplicação."
    echo "   Essa conta pode executar a API, mas não pode administrar service accounts"
    echo "   nem criar/listar chaves para este setup."
    echo ""
    echo "   Autentique-se com uma conta humana que tenha permissões de IAM e selecione-a:"
    echo "     gcloud auth login"
    echo "     gcloud config set account SEU_EMAIL@DOMINIO.COM"
    echo ""
    echo "   Depois execute novamente: make setup-creds"
    exit 1
fi

# Verificar projeto
echo "✓ Verificando projeto GCP..."
gcloud config set project "$PROJECT_ID" --quiet
echo "  Projeto: $PROJECT_ID"
echo ""

# Verificar se conta de serviço existe
echo "✓ Verificando conta de serviço: $SERVICE_ACCOUNT_EMAIL"
if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" \
    --project="$PROJECT_ID" &> /dev/null; then
    echo "⚠️  Não foi possível consultar a conta de serviço '$SERVICE_ACCOUNT'."
    echo "   Isso pode significar que ela não existe ou que '$CURRENT_ACCOUNT'"
    echo "   não tem a permissão iam.serviceAccounts.get."
    echo "   O setup espera que essa conta já exista e não tentará criá-la automaticamente."
    echo "   Confirme o projeto, a conta ativa e as permissões de IAM antes de continuar."
    exit 1
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

echo "🔐 Configurando segredo do Google Measurement Protocol..."
read -r -s -p "Informe o Measurement Protocol API secret (não será exibido): " MEASUREMENT_PROTOCOL_API_SECRET
echo
if [ -z "$MEASUREMENT_PROTOCOL_API_SECRET" ]; then
    echo "❌ O Measurement Protocol API secret é obrigatório para o ambiente DEV."
    exit 1
fi

MEASUREMENT_PROTOCOL_API_SECRET="$MEASUREMENT_PROTOCOL_API_SECRET" python3 - "$KEY_PATH" <<'PY'
import json
import os
import sys

key_path = sys.argv[1]
with open(key_path, encoding="utf-8") as key_file:
    credentials = json.load(key_file)

credentials["measurement_protocol_api_secret"] = os.environ["MEASUREMENT_PROTOCOL_API_SECRET"]
with open(key_path, "w", encoding="utf-8") as key_file:
    json.dump(credentials, key_file, indent=2)
    key_file.write("\n")
PY
chmod 600 "$KEY_PATH"

echo "✓ Chave gerada em: $KEY_PATH"
echo ""

# Conceder permissões necessárias
echo "✓ Configurando permissões..."

ROLES=(
    "roles/bigquery.jobUser"
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
echo "   2. Inicie o container com :"
echo "       \"make up\" ou \"docker compose up -d\""
echo ""
echo "   3. Verifique se está funcionando:"
echo "      curl http://localhost:8080/"
echo ""
