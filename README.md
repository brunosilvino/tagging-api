# Tagging Validation API

![Status](https://img.shields.io/badge/status-development-yellow)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen)

API Flask para validação de eventos de Analytics com suporte a BigQuery, Firestore, deduplicação por sessão e integração com Google Analytics 4 (GA4).

## 📋 Índice

- [Quick Start](#-quick-start)
- [Arquitetura](#-arquitetura)
- [Desenvolvimento Local](#-desenvolvimento-local)
- [Deploy em Produção](#-deploy-em-produção)
- [Referência de Endpoints](#-referência-de-endpoints)
- [Segurança](#-segurança)
- [Troubleshooting](#-troubleshooting)

---

## 🚀 Quick Start

### Pré-requisitos

- **Docker & Docker Compose**: [Instalar](https://docs.docker.com/get-docker/)
- **gcloud CLI**: [Instalar](https://cloud.google.com/sdk/docs/install)
- **Autenticação GCP**: `gcloud auth login`

### Desenvolvimento Local (3 passos)

```bash
# 1️⃣ Clonar repositório
git clone https://github.com/brunosilvino/tagging-api.git
cd tagging-api

# 2️⃣ Gerar credenciais (key.json) automaticamente
make setup-creds
# Ou manualmente: ./deployment/setup-credentials.sh

# 3️⃣ Iniciar containers
make up
# Ou manualmente: docker compose up -d
```

**Pronto!** A API está em `http://localhost:8080`

```bash
# Health check
curl http://localhost:8080/

# Swagger UI
open http://localhost:8080/apidocs
```

---

## 🏗️ Arquitetura

```
┌──────────────────────────────────────────────────────────────┐
│                     Clients                                  │
│              (Web, tagging.js, GA4)                          │
└────────────────────┬─────────────────────────────────────────┘
                     │
┌────────────────────▼───────────────────────────────────────┐
│                  Flask API (:8080)                         │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  4-Layer Validation:                                 │  │
│  │  1. Deduplication  (TTL Cache, 2s default)           │  │
│  │  2. Taxonomy       (snake_case, reserved names)      │  │
│  │  3. Schema         (Firestore rules)                 │  │ 
│  │  4. Google MP      (GA4 Debug Protocol)              │  │
│  └──────────────────────────────────────────────────────┘  │
└────────┬─────────────────────────────┬────────────────────-┘
         │                             │
   ┌─────▼──────┐            ┌────────▼────────┐
   │  Firestore │            │   BigQuery      │
   │ (rules)    │            │  (analytics)    │
   └────────────┘            └─────────────────┘
```

### Componentes

| Componente | Descrição |
|-----------|-----------|
| **Flask API** | Servidor HTTP em Python |
| **Deduplication** | Cache TTL para evitar eventos duplicados |
| **Taxonomy Validation** | Verifica padrões de nomenclatura |
| **Firestore** | Armazena regras de validação |
| **BigQuery** | Carrega dados analíticos |
| **Docker** | Containerização (Python 3.10-slim + Gunicorn) |

---

## 🔧 Desenvolvimento Local

### Estrutura de pastas

```
tagging-api/
├── api/
│   ├── main.py                     # Aplicação Flask principal
│   ├── config.py                   # Configuração central (versão, etc)
│   ├── requirements.txt            # Dependências Python
│   ├── Dockerfile                  # Imagem Docker (produção)
│   └── .dockerignore               # Arquivos excluídos do build
├── deployment/
│   ├── setup-credentials.sh        # Gerar key.json automaticamente
│   ├── validate-credentials.sh     # Validar key.json
│   ├── deploy.sh                   # Deploy manual no Cloud Run
│   └── setup-github-actions.sh     # Configurar WIF
├── .github/workflows/
│   └── deploy.yml                  # CI/CD pipeline
├── docker-compose.yml              # Composição local (development)
├── Makefile                        # Comandos úteis
├── .gitignore                      # Excluir do repositório
├── .dockerignore                   # Excluir do Docker
├── key.json                        # ⚠️ Credenciais (NUNCA comitar)
└── README.md                       # Este arquivo
```

### Comandos Úteis

```bash
# Setup inicial
make setup-creds            # Gerar key.json
make validate-creds         # Validar credenciais

# Docker
make build                  # Build da imagem
make up                     # Iniciar containers
make down                   # Parar containers
make logs                   # Ver logs

# Diagrama de arquitetura
make diagram                # Gerar PNG da arquitetura
make clean                  # Limpar arquivos gerados

# Ajuda
make help                   # Listar todos os comandos
```

### Configuração de Desenvolvimento

**docker-compose.yml** - variáveis de ambiente:

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `DEDUP_TTL` | `2.0` | Janela de deduplicação (segundos) |
| `DEDUP_MAXSIZE` | `1000` | Tamanho máximo do cache |
| `ADMIN_KEY` | vazio | Chave para proteger `/clear-cache` |
| `FLASK_ENV` | `development` | environment (development/production) |

Para mudar:

```yaml
# docker-compose.yml
environment:
  - DEDUP_TTL=5.0           # Aumentar janela
  - ADMIN_KEY=seu-secret    # Proteger endpoints
```

### Versionamento

A versão é centralizada em `api/config.py`:

```python
__version__ = "0.0.1"
```

**Convenção semântica:**
- `0.0.x` - Desenvolvimento (instável)
- `0.x.0` - Features em preview
- `1.0.0` - Primeira versão estável
- `1.x.0` - Features compatíveis
- `x.0.0` - Breaking changes

---

## 📡 Referência de Endpoints

### Health Check

```bash
curl http://localhost:8080/
```

**Resposta:**
```json
{
  "status": "ONLINE",
  "version": "0.0.1",
  "app": "Tagging Validation API",
  "docs": "/apidocs"
}
```

### Validação (POST /validate)

Valida eventos com 4 camadas:

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

**Camadas de Validação:**
1. **Deduplication** - Evento duplicado em curto intervalo?
2. **Taxonomy** - Respeita convenções (snake_case)?
3. **Schema** - Atende regras do Firestore?
4. **Google MP** - Envia para GA4 Debug Protocol?

### Carregar Mapa (POST /loadmap)

Carrega regras do BigQuery para Firestore:

```bash
curl -X POST http://localhost:8080/loadmap \
  -H "Content-Type: application/json" \
  -d '{"map_id": "00001"}'
```

### Limpar Cache (POST /clear-cache)

Remove documentos do Firestore (⚠️ requer `ADMIN_KEY`):

```bash
curl -X POST http://localhost:8080/clear-cache \
  -H "Content-Type: application/json" \
  -H "X-ADMIN-KEY: seu-secret" \
  -d '{"confirm": true}'
```

---

## 🚀 Deploy em Produção

### Pré-requisitos

1. **Projeto GCP**: `tagging-api-481123`
2. **gcloud CLI configurado**: `gcloud config set project tagging-api-481123`
3. **Cloud Run habilitado**: `gcloud services enable run`

### Opção 1: Deploy Automático (GitHub Actions)

**Primeira vez - Configurar WIF:**

```bash
./deployment/setup-github-actions.sh
```

Copie os valores para GitHub Secrets em:
`https://github.com/brunosilvino/tagging-api/settings/secrets/actions`

- `WIF_PROVIDER`
- `WIF_SERVICE_ACCOUNT`
- `ADMIN_KEY` (gere com: `openssl rand -hex 32`)

**Deploy automático:**
```bash
git push origin main  # Triggers CI/CD
```

### Opção 2: Deploy Manual

```bash
./deployment/deploy.sh [TAG]
```

### Monitoramento

```bash
# Logs em tempo real
gcloud run logs read tagging-api --region southamerica-east1 --follow

# Status do serviço
gcloud run services describe tagging-api --region southamerica-east1

# Testar endpoint em produção
URL=$(gcloud run services describe tagging-api \
  --region southamerica-east1 \
  --format='value(status.url)')
curl "$URL/"
```

---

## 🔐 Segurança

### Proteção de Credenciais

✅ **Arquivos Ignorados:**
- `key.json` → `.gitignore` (nunca vai pro repo)
- `key.json` → `.dockerignore` (nunca vai pra produção)

✅ **Variáveis de Ambiente:**
- Segredos passados via `docker compose` ou Cloud Run
- Nunca hardcoded no código

✅ **Authentication:**
- Desenvolvimento: Conta de serviço local (`developer@tagging-api-481123`)
- Produção: Workload Identity Federation (sem chaves)

✅ **Endpoints Protegidos:**
- `/clear-cache` requer header `X-ADMIN-KEY`

### Geração de Credenciais Segura

```bash
# Gerar automaticamente (recomendado)
make setup-creds

# Validar
make validate-creds
```

O script:
- ✓ Verifica autenticação com `gcloud`
- ✓ Usa conta de serviço existente
- ✓ Cria apenas as permissões necessárias
- ✓ Substitui chaves antigas automaticamente

---

## 🆘 Troubleshooting

### Container não inicia

```bash
# Ver logs
docker compose logs tagging-api

# Reconstruir
docker compose build --no-cache
docker compose up -d
```

### `gcloud` não encontrado

```bash
brew install --cask google-cloud-sdk
gcloud --version
```

### Erro ao gerar credenciais

```bash
# Verificar autenticação
gcloud auth list

# Reautenticar se necessário
gcloud auth login
gcloud config set project tagging-api-481123
```

### Erro ao conectar Firestore/BigQuery

- Verifique `key.json` na raiz do projeto
- Confirme `GOOGLE_CLOUD_PROJECT=tagging-api-481123` no docker-compose
- Valide IAM roles: `make validate-creds`

### Deploy falha no GitHub Actions

- Confirme WIF configurado: `./deployment/setup-github-actions.sh`
- Verifique secrets em: `Settings → Secrets → Actions`
- Consulte logs do workflow em: `Actions → Deploy`

---

## 📚 Referências

| Recurso | Link |
|---------|------|
| Flask | [palletsprojects.com](https://flask.palletsprojects.com/) |
| Cloud Run | [cloud.google.com/run](https://cloud.google.com/run/docs) |
| Firestore | [cloud.google.com/firestore](https://cloud.google.com/firestore/docs) |
| BigQuery | [cloud.google.com/bigquery](https://cloud.google.com/bigquery/docs) |
| WIF | [cloud.google.com/workload-identity](https://cloud.google.com/docs/authentication/workload-identity-federation) |

---

## 📊 Status do Projeto

| Item | Status |
|------|--------|
| API Core | ✅ Estável |
| 4-Layer Validation | ✅ Funcional |
| Docker/Compose | ✅ Pronto |
| Cloud Run Deploy | ✅ Automático |
| Documentação | ✅ Completa |
| Segurança | ✅ Implementada |

---

## 🤝 Contribuindo

1. Fork o repositório
2. Crie uma branch (`git checkout -b feature/xyz`)
3. Faça commits (`git commit -m "feat: xyz"`)
4. Push (`git push origin feature/xyz`)
5. Abra um Pull Request

---

## 📝 Licença

MIT License - veja LICENSE.md para detalhes.

---

## 📞 Suporte

Para dúvidas ou problemas:
1. Consulte os **logs**: `docker compose logs tagging-api`
2. Verifique **Troubleshooting** acima
3. Abra uma **Issue** no GitHub
