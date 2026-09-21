#!/bin/bash
# Script para validar se key.json existe e está correto
# Uso: ./deployment/validate-credentials.sh

set -e

KEY_PATH="./key.json"

echo "🔍 Validando Credenciais"
echo "========================="
echo ""

# Verificar se arquivo existe
if [ ! -f "$KEY_PATH" ]; then
    echo "❌ Arquivo $KEY_PATH não encontrado!"
    echo ""
    echo "📝 Para gerar, execute:"
    echo "   ./deployment/setup-credentials.sh"
    echo ""
    exit 1
fi

echo "✓ Arquivo encontrado"

# Validar JSON
if command -v jq &> /dev/null; then
    if ! jq empty "$KEY_PATH" 2>/dev/null; then
        echo "❌ JSON inválido em $KEY_PATH"
        exit 1
    fi
    echo "✓ JSON válido"
    
    # Verificar campos obrigatórios
    for field in "type" "project_id" "private_key_id" "private_key" "client_email" "client_id" "measurement_protocol_api_secret"; do
        if ! jq -e ".$field" "$KEY_PATH" > /dev/null 2>&1; then
            echo "❌ Campo obrigatório faltando: $field"
            exit 1
        fi
    done
    
    echo "✓ Todos os campos obrigatórios presentes"
    
    # Exibir informações
    PROJECT_ID=$(jq -r '.project_id' "$KEY_PATH")
    SERVICE_ACCOUNT=$(jq -r '.client_email' "$KEY_PATH")
else
    # Fallback sem jq
    if ! python3 -m json.tool "$KEY_PATH" > /dev/null 2>&1; then
        echo "❌ JSON inválido em $KEY_PATH"
        exit 1
    fi
    echo "✓ JSON válido"
    
    # Verificar se arquivo contém campos obrigatórios
    for field in "type" "project_id" "private_key_id" "private_key" "client_email" "client_id" "measurement_protocol_api_secret"; do
        if ! grep -q "\"$field\"" "$KEY_PATH"; then
            echo "❌ Campo obrigatório faltando: $field"
            exit 1
        fi
    done
    
    echo "✓ Todos os campos obrigatórios presentes"
    
    # Exibir informações
    PROJECT_ID=$(python3 -c "import json; print(json.load(open('$KEY_PATH'))['project_id'])")
    SERVICE_ACCOUNT=$(python3 -c "import json; print(json.load(open('$KEY_PATH'))['client_email'])")
fi

echo ""
echo "📋 Credenciais:"
echo "  Project: $PROJECT_ID"
echo "  Service Account: $SERVICE_ACCOUNT"
echo ""

# Tentar validar com gcloud (opcional)
if command -v gcloud &> /dev/null; then
    echo "🔐 Testando autenticação..."
    PREVIOUS_ACCOUNT=$(gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>/dev/null | head -n 1)
    if gcloud auth activate-service-account --key-file="$KEY_PATH" --quiet 2>/dev/null; then
        echo "✓ Autenticação bem-sucedida"
    else
        echo "⚠️  Aviso: Falha ao autenticar com gcloud"
        echo "   (Pode ser normalmente ignorado em CI/CD)"
    fi

    if [ -n "$PREVIOUS_ACCOUNT" ]; then
        gcloud config set account "$PREVIOUS_ACCOUNT" --quiet >/dev/null 2>&1 || true
    else
        gcloud config unset account --quiet >/dev/null 2>&1 || true
    fi
fi

echo ""
echo "✅ Credenciais válidas!"
