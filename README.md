# Tagging Validation API

API Flask para validar eventos de Analytics usando PostgreSQL como banco único. A tabela `map_collection` é particionada por hash de `map_id` e armazena os parâmetros em `JSONB`. A tabela `map_taxonomy` define as regras usadas pela validação de taxonomia.

## Desenvolvimento local

Pré-requisitos: Docker e Docker Compose.

### Comandos Make

O `Makefile` define os comandos abaixo. Execute-os a partir da raiz do projeto:

| Comando | Descrição |
| --- | --- |
| `make` ou `make help` | Exibe a lista de comandos disponíveis. É o comando padrão. |
| `make build` | Constrói todas as imagens Docker sem iniciar os serviços. |
| `make api` | Reconstrói a imagem e reinicia somente o serviço da API, sem recriar as dependências. |
| `make db` | Recria o volume do PostgreSQL, executa novamente os scripts de inicialização e recarrega os CSVs. Os dados atuais do banco são apagados. |
| `make pgadmin` | Inicia o pgAdmin e o disponibiliza em `http://localhost:5050`. |
| `make up` | Inicia todos os serviços definidos no Docker Compose. A API fica disponível em `http://localhost:8080`. |
| `make down` | Para e remove os containers e volumes da stack local, apagando os dados persistidos do PostgreSQL e do pgAdmin. |
| `make logs` | Exibe e acompanha continuamente os logs do serviço da API. Use `Ctrl+C` para sair. |
| `make query` | Abre um terminal PostgreSQL interativo no container. |
| `make diagram` | Gera o diagrama de arquitetura usando o container da API. |
| `make clean` | Remove diagramas e arquivos gerados (`.png`, `.html`, `.dot`, `.svg` e `diagrams_output/`). |

Para executar uma consulta única sem abrir o prompt interativo, passe a SQL pela
variável `QUERY`:

```bash
make query QUERY="SELECT count(*) FROM map_collection;"
make query QUERY="SELECT * FROM map_taxonomy ORDER BY param_name;"
```

```bash
make up
curl http://localhost:8080/
open http://localhost:8080/apidocs
```

A stack local sobe PostgreSQL em `localhost:5432` e a API em `localhost:8080`. O schema é criado automaticamente. Para parar os serviços:

```bash
make down
```

O pgAdmin fica disponível em `http://localhost:5050` para visualizar e consultar
os dados. Login do pgAdmin: `admin@tagging.dev` / `tagging`. Ao criar o
servidor, use `postgres` como host, banco `tagging`, usuário `tagging` e senha
`tagging`.

O servidor `Tagging PostgreSQL` já é importado automaticamente a partir de
`pgadmin/servers.json` e aparece conectado no grupo `Tagging API`.

A pasta `database/` é a fonte da base local:

- `001_schema.sql`: as tabelas, partições e índices.
- `002_seed.sql`: registros demonstrativos.
- `003_import_map_collection.sql`: importa `map_collection.csv` para `map_collection`.
- `004_import_map_taxonomy.sql`: importa `map_taxonomy.csv`.

Os scripts de inicialização do PostgreSQL só rodam quando o volume é criado.
Para recriar a base local a partir desses arquivos, use `docker compose down -v`
e depois `make up`.

Variáveis principais: `DATABASE_URL`, `API_KEY`, `ADMIN_KEY`, `CORS_ORIGINS` e `DEDUP_TTL`.

## Importação de mapas

`POST /loadmaps` grava mapas diretamente no PostgreSQL. O corpo deve conter os eventos, pois não existe mais uma fonte BigQuery ou cache Redis:

```json
[
  {
    "map_id": "00001",
    "map_version": "20260105",
    "events": [
      {
        "event_id": "evt-home-click",
        "event_name": "click",
        "params": {"page_path": "/", "component": "button"}
      }
    ]
  }
]
```

Os endpoints principais permanecem disponíveis: `GET /`, `GET /map`, `GET /event/<event_id>`, `GET /events`, `DELETE /events`, `POST /validate` e as validações individuais.

## Deploy no Render

O arquivo `render.yaml` define três recursos:

- Web Service Docker para a API.
- PostgreSQL gerenciado.
- Static Site para `frontend/`.

Configure `API_KEY`, `ADMIN_KEY` e `CORS_ORIGINS` como variáveis secretas ou de ambiente no Render. O banco é injetado na API por `DATABASE_URL`. Não são necessários GCP, Redis, VPC, `key.json` ou service accounts.

Para deploy automático via GitHub Actions, crie o secret `RENDER_DEPLOY_HOOK` com o Deploy Hook do Web Service. O workflow dispara esse hook em pushes para `main`.

## Arquitetura

```text
Frontend (Render Static Site)
          |
          v
API Flask + Gunicorn (Render Web Service)
          |
          v
PostgreSQL (Render): map_collection particionada por hash(map_id) + map_taxonomy
```

A validação é composta por deduplicação, taxonomia, schema e, opcionalmente, Google Measurement Protocol. A camada Google continua sendo uma integração externa opcional; ela não é usada como banco ou infraestrutura de deploy.
