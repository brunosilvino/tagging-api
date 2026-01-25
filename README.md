# Tagging Validation API

API Flask para validação de eventos de Analytics com suporte a BigQuery, Firestore, deduplicação por sessão e integração com Google Analytics 4 (GA4).

## 📋 Requisitos

- Docker e Docker Compose
- `gcloud` CLI (para deploy em Cloud Run)
- Python 3.10+ (se rodar localmente sem container)
- Arquivo `key.json` de credenciais GCP na raiz do projeto

## 🔢 Versionamento

A versão da API é centralizada em `api/config.py`:

```python
__version__ = "0.0.1"
```

**Para atualizar a versão:**
1. Edite apenas `api/config.py`
2. Commit e push (a versão será propagada automaticamente)

**Onde a versão aparece:**
- Health check endpoint (`GET /`) retorna `{"version": "0.0.1"}`
- Swagger UI (`/apidocs`) exibe a versão
- Logs de inicialização: `"Tagging Validation API v0.0.1 - ..."`

**Convenção de versionamento semântico:**
- `0.0.x` - Desenvolvimento inicial (instável, breaking changes frequentes)
- `0.x.0` - Features novas em preview
- `1.0.0` - Primeira versão estável em produção
- `1.x.0` - Features backward-compatible
- `x.0.0` - Breaking changes

## 🚀 Início Rápido

### Desenvolvimento Local (Docker Compose)

```bash
# Inicia o container em background
docker compose up -d

# Verifica os logs
docker compose logs -f tagging-api

# Acessa a API
curl http://localhost:8080/

# Resposta esperada:
# {
#   "status": "ONLINE",
#   "version": "0.0.1",
#   "app": "Tagging Validation API",
#   "docs": "/apidocs"
# }

# Swagger UI
#### 1. Health Check
```bash
curl -X GET http://localhost:8080/
```
Retorna: 
```json
{
  "status": "ONLINE",
  "version": "0.0.1",
  "app": "Tagging Validation API",
  "docs": "/apidocs"
}
```

### Endpoints Principais

#### 1. Health Check
```bash
curl -X GET http://localhost:8080/
```
Retorna: `{"status": "ONLINE", "docs": "/apidocs"}`

#### 2. Carregar Mapa do BigQuery
```bash
curl -X POST http://localhost:8080/loadmap \
  -H "Content-Type: application/json" \
  -d '{"map_id": "00001"}'
```
Carrega regras do BigQuery e insere no Firestore.

#### 3. Limpar Cache Firestore
```bash
curl -X POST http://localhost:8080/clear-cache \
  -H "Content-Type: application/json" \
  -d '{"confirm": true}'
```
Remove todos os documentos do Firestore (use com cuidado).

#### 4. Validação Completa (4 Camadas)
```bash
curl -X POST http://localhost:8080/validate \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "select_content",
    "params": {
      "content_type": "article",
      "item_id": "12345"
    },
    "measurement_id": "G-NF7LZK2M10",
    "api_secret": "7IrA3QyPTJaCUe1edtAh3w"
  }'
```

As 4 camadas de validação:
1. **Deduplication**: Detecta eventos duplicados em curto intervalo (TTL configurável, padrão 2s)
2. **Taxonomy**: Verifica padrões de nomenclatura (snake_case, prefixos reservados)
3. **Schema**: Valida contra regras do Firestore
4. **Google MP**: Envia para Google Analytics Debug Protocol

## 🔧 Configuração

### Variáveis de Ambiente

| Var | Padrão | Descrição |
|-----|--------|-----------|
| `GOOGLE_CLOUD_PROJECT` | `tagging-api-481123` | Projeto GCP para BigQuery |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/secrets/key.json` | Caminho da chave GCP |
| `DEDUP_TTL` | `2.0` | Janela de deduplicação (segundos) |
| `DEDUP_MAXSIZE` | `1000` | Tamanho máximo do cache de dedup |
| `ADMIN_KEY` | (vazio) | Chave para proteger `/clear-cache` |
| `FLASK_ENV` | `development` | Ambiente (development/production) |
| `PORT` | `8080` | Porta da aplicação |

### Docker Compose

Edite `docker-compose.yml` para ajustar variáveis ou mounts:

```yaml
environment:
  - DEDUP_TTL=3.0          # Aumentar janela de dedup
  - DEDUP_MAXSIZE=2000     # Aumentar cache
  - ADMIN_KEY=seu-secret   # Proteger /clear-cache
```

## 📦 Deploy em Produção

### 1. Preparar GCP (primeira vez)

```bash
# Instalar gcloud CLI
brew install google-cloud-sdk

# Inicializar e autenticar
gcloud init
gcloud auth login
gcloud config set project tagging-api-481123

# Configurar Workload Identity Federation (WIF)
chmod +x deployment/setup-github-actions.sh
./deployment/setup-github-actions.sh
```

O script exibirá três valores para adicionar como GitHub Secrets:
- `WIF_PROVIDER`
- `WIF_SERVICE_ACCOUNT`
- `ADMIN_KEY`

### 2. Adicionar GitHub Secrets

Vá para: `https://github.com/brunosilvino/tagging-api/settings/secrets/actions`

Adicione:
- `WIF_PROVIDER`: Copie do output do script
- `WIF_SERVICE_ACCOUNT`: Copie do output do script
- `ADMIN_KEY`: Gere com `openssl rand -hex 32`

### 3. Deploy Automático

Faça push para `main`:
```bash
git add .
git commit -m "feat: novo recurso"
git push origin main
```

GitHub Actions iniciará automaticamente:
1. Build da imagem Docker
2. Push para Artifact Registry
3. Deploy no Cloud Run (`southamerica-east1`)

Monitore em: `https://github.com/brunosilvino/tagging-api/actions`

### 4. Deploy Manual (quando necessário)

```bash
# Se o gcloud CLI já está configurado
./deployment/deploy.sh

# Com tag específica (ex: commit hash)
./deployment/deploy.sh abc123def456
```

## 🔍 Monitoramento

### Logs do Cloud Run

```bash
gcloud run logs read tagging-api --region southamerica-east1 --limit 50
```

### Status do Serviço

```bash
gcloud run services describe tagging-api --region southamerica-east1
```

### Testar Endpoint em Produção

```bash
# Obter URL
URL=$(gcloud run services describe tagging-api \
  --region southamerica-east1 \
  --format='value(status.url)')

# Health check
curl -X GET "$URL/"

# Validação
curl -X POST "$URL/validate" \
  -H "Content-Type: application/json" \
```
tagging-api/
├── api/
│   ├── main.py                 # Aplicação Flask
│   ├── config.py               # Configuração central (versão, nome)
│   ├── requirements.txt         # Dependências Python
│   ├── Dockerfile              # Imagem de produção
│   └── .dockerignore           # Excluir do build Docker
├── deployment/
│   ├── deploy.sh               # Deploy manual no Cloud Run
│   ├── setup-github-actions.sh # Configurar WIF no GCP
│   └── list-wif-credentials.sh # Listar credenciais WIF existentes
├── .github/
│   └── workflows/
│       └── deploy.yml          # Pipeline CI/CD
├── docker-compose.yml          # Dev local com Docker
├── .gitignore                  # Excluir do repositório
├── .gcloudignore               # Excluir de deploys via gcloud
├── key.json                    # Credenciais GCP (NÃO comitar)
└── README.md                   # Este arquivo
``` .github/
│   └── workflows/
│       └── deploy.yml          # Pipeline CI/CD
├── docker-compose.yml          # Dev local com Docker
├── .gitignore                  # Excluir do repositório
├── .gcloudignore               # Excluir de deploys via gcloud
├── key.json                    # Credenciais GCP (NÃO comitar)
└── README.md                   # Este arquivo
```

## 🔐 Segurança

- ✅ `key.json` é ignorado pelo Git e Docker
- ✅ Segredos passados via variáveis de ambiente (não hardcoded)
- ✅ GitHub Actions usa Workload Identity Federation (sem chaves)
- ✅ Cloud Run autenticado via conta de serviço dedicada
- ✅ ADMIN_KEY protege endpoints sensíveis

## 🧪 Testes Locais

### Testar Deduplicação

```bash
# Primeira chamada - OK
curl -X POST http://localhost:8080/validate \
  -H "Content-Type: application/json" \
  -H "X-CLIENT-ID: client1" \
  -d '{"event_name":"test_event","params":{}}'

# Segunda chamada idêntica em < 2s - ERRO (duplicado)
curl -X POST http://localhost:8080/validate \
  -H "Content-Type: application/json" \
  -H "X-CLIENT-ID: client1" \
  -d '{"event_name":"test_event","params":{}}'

# Esperar 2+ segundos e tentar novamente - OK
sleep 3
curl -X POST http://localhost:8080/validate \
  -H "Content-Type: application/json" \
  -H "X-CLIENT-ID: client1" \
  -d '{"event_name":"test_event","params":{}}'
```

### Testar com Admin Key

```bash
# Sem key ou key incorreta - Unauthorized
curl -X POST http://localhost:8080/clear-cache \
  -H "Content-Type: application/json" \
  -d '{"confirm": true}'

# Com key correta (se ADMIN_KEY definida no docker-compose)
curl -X POST http://localhost:8080/clear-cache \
  -H "Content-Type: application/json" \
  -H "X-ADMIN-KEY: seu-secret" \
  -d '{"confirm": true}'
```

## 📝 Logs e Debugging

### Logs Locais

```bash
docker compose logs -f tagging-api
```

### Logs em Produção

```bash
gcloud run logs read tagging-api --region southamerica-east1 --follow
```

### Verificar Variáveis de Ambiente (Cloud Run)

```bash
gcloud run services describe tagging-api \
  --region southamerica-east1 \
  --format='value(spec.template.spec.containers[0].env)'
```

## 🔄 Troubleshooting

### Container não inicia

```bash
# Verificar logs
docker compose logs tagging-api

# Reconstruir
docker compose build --no-cache
docker compose up -d
```

### `gcloud` command not found

```bash
brew install google-cloud-sdk
gcloud --version
```

### Erro ao conectar com Firestore/BigQuery

- Verifique `key.json` na raiz
- Confirme que `GOOGLE_CLOUD_PROJECT` está correto
- Verifique IAM roles da conta de serviço no GCP

### Deploy falha no GitHub Actions

- Confirme que `WIF_PROVIDER` e `WIF_SERVICE_ACCOUNT` estão nos GitHub Secrets
- Verifique que o WIF foi configurado corretamente (rode `./deployment/setup-github-actions.sh` novamente)
- Consulte os logs do workflow em GitHub Actions

## 📚 Referências

- [Flask Documentation](https://flask.palletsprojects.com/)
- [Google Cloud Run](https://cloud.google.com/run/docs)
- [Firestore Documentation](https://cloud.google.com/firestore/docs)
- [BigQuery Documentation](https://cloud.google.com/bigquery/docs)
- [Workload Identity Federation](https://cloud.google.com/docs/authentication/workload-identity-federation)

## 🤝 Suporte

Para dúvidas ou problemas, consulte os logs e verifique a seção de Troubleshooting acima.
