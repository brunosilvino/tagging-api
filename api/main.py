import os
import logging
import json
import hashlib
import time
import requests
import re
from urllib.parse import quote_plus
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.cloud import bigquery
from cachetools import TTLCache
from flasgger import Swagger
import threading
import redis
import google.auth
import secrets
from config import __version__, APP_NAME, APP_DESCRIPTION

# --- CONFIGURAÇÃO ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configurar CORS com suporte a variáveis de ambiente
FLASK_ENV = os.environ.get('FLASK_ENV', 'development')
CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '*')

# Em produção, CORS_ORIGINS pode ser uma lista separada por vírgula: "https://exemplo.com,https://app.exemplo.com"
if FLASK_ENV == 'production' and CORS_ORIGINS != '*':
    allowed_origins = [origin.strip() for origin in CORS_ORIGINS.split(',')]
else:
    allowed_origins = "*"  # Permitir todas as origens em desenvolvimento

CORS(app, resources={
    r"/*": {
        "origins": allowed_origins,
        "methods": ["GET", "POST", "OPTIONS", "DELETE"],
        "allow_headers": ["Content-Type", "Authorization", "X-CLIENT-ID", "X-ADMIN-KEY"],
        "expose_headers": ["X-Response-Time"],
        "max_age": 86400  # Cache preflight por 24 horas
    }
})

logger.info(f"CORS configurado para {FLASK_ENV}: origins={allowed_origins}")

@app.before_request
def start_response_timer():
    request.response_start_time = time.perf_counter()

@app.after_request
def add_response_time_header(response):
    start_time = getattr(request, "response_start_time", None)
    if start_time is not None:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.2f}ms"
    return response

# Configuração do Swagger
app.config['SWAGGER'] = {
    'title': APP_NAME,
    'uiversion': 3,
    'description': APP_DESCRIPTION,
    'version': __version__,
    'securityDefinitions': {
        'bearerAuth': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header',
            'description': 'Informe o valor no formato: Bearer <API_KEY>.',
        }
    },
    'security': [{'bearerAuth': []}],
    'definitions': {
        'ErrorResponse': {
            'type': 'object',
            'properties': {
                'error': {'type': 'string'},
                'details': {'type': 'array', 'items': {'type': 'string'}}
            },
            'required': ['error']
        },
        'StatusErrorResponse': {
            'type': 'object',
            'properties': {
                'status': {'type': 'string', 'example': 'ERROR'},
                'message': {'type': 'string'}
            },
            'required': ['status', 'message']
        },
        'EventRule': {
            'type': 'object',
            'properties': {
                'event_id': {'type': 'string'},
                'metadata': {
                    'type': 'object',
                    'properties': {
                        'map_id': {'type': 'string'},
                        'map_version': {'type': 'string'}
                    }
                },
                'event_name': {'type': 'string'},
                'params': {'type': 'object', 'additionalProperties': {}}
            },
            'required': ['event_name', 'params']
        },
        'EventList': {
            'type': 'array',
            'items': {'$ref': '#/definitions/EventRule'}
        },
        'LoadMapsResponse': {
            'type': 'object',
            'properties': {
                'status': {'type': 'string', 'enum': ['SUCCESS', 'PARTIAL', 'ERROR']},
                'mode': {'type': 'string', 'example': 'COLD_START_COMPLETE'},
                'maps': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'map_id': {'type': 'string'},
                            'map_version': {'type': 'string'},
                            'status': {'type': 'string', 'enum': ['SUCCESS', 'EMPTY', 'ERROR']},
                            'events': {'type': 'integer'},
                            'message': {'type': 'string'}
                        },
                        'required': ['map_id', 'status']
                    }
                }
            },
            'required': ['status', 'mode', 'maps']
        },
        'ClearCacheResponse': {
            'type': 'object',
            'properties': {
                'status': {'type': 'string', 'example': 'SUCCESS'},
                'deleted': {'type': 'integer', 'example': 42}
            },
            'required': ['status', 'deleted']
        },
        'MapEventsResponse': {
            'type': 'object',
            'properties': {
                'map_id': {'type': 'string'},
                'map_version': {'type': 'string', 'x-nullable': True},
                'count': {'type': 'integer'},
                'events': {'$ref': '#/definitions/EventList'}
            },
            'required': ['map_id', 'count', 'events']
        },
        'FilteredEventsResponse': {
            'type': 'object',
            'properties': {
                'parameter': {'type': 'string'},
                'value': {'type': 'string'},
                'count': {'type': 'integer'},
                'events': {'$ref': '#/definitions/EventList'}
            },
            'required': ['parameter', 'value', 'count', 'events']
        },
        'ValidationLayer': {
            'type': 'object',
            'properties': {
                'status': {'type': 'string', 'enum': ['OK', 'ERROR', 'WARNING', 'SKIPPED', 'SUGGESTIONS']},
                'layer': {'type': 'string'},
                'message': {'type': 'string'},
                'issues': {'type': 'array', 'items': {'type': 'string'}},
                'suggestions': {'type': 'object', 'additionalProperties': {'type': 'array', 'items': {'type': 'string'}}},
                'google_feedback': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': {}}}
            },
            'required': ['status']
        },
        'ValidationResponse': {
            'type': 'object',
            'properties': {
                'summary': {'type': 'string'},
                'details': {
                    'type': 'object',
                    'properties': {
                        'event': {'type': 'string'},
                        'valid': {'type': 'boolean'},
                        'layers': {
                            'type': 'object',
                            'additionalProperties': {'$ref': '#/definitions/ValidationLayer'}
                        }
                    },
                    'required': ['event', 'valid', 'layers']
                }
            },
            'required': ['summary', 'details']
        }
    }
}
swagger = Swagger(app)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
BQ_TABLE = "tagging-api-481123.tagging_maps.collection_maps"
REDIS_URL = os.environ.get("REDIS_URL")
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_DB = int(os.environ.get("REDIS_DB", 0))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")
REDIS_PREFIX = os.environ.get("REDIS_PREFIX", "tagging-api")
API_KEY = os.environ.get("API_KEY")

def require_api_key():
    """Valida a chave da API enviada no header Authorization."""
    if not API_KEY:
        logger.error("API_KEY não configurada")
        return jsonify({"error": "Autenticação da API não configurada."}), 500

    authorization = request.headers.get("Authorization", "").strip()
    scheme, separator, provided_key = authorization.partition(" ")
    if separator:
        if scheme.lower() != "bearer" or not provided_key.strip():
            return jsonify({"error": "Authorization Bearer obrigatório."}), 401
    else:
        # Compatibilidade com o campo apiKey do Swagger, que envia somente a chave.
        provided_key = authorization

    if not provided_key:
        return jsonify({"error": "Authorization Bearer obrigatório."}), 401

    if not secrets.compare_digest(provided_key, API_KEY):
        return jsonify({"error": "Unauthorized"}), 401

    return None

@app.before_request #registra uma função que o Flask executa antes de qualquer endpoint.
def authenticate_api_request():
    """Exige autenticação Bearer nos endpoints protegidos em produção."""
    if FLASK_ENV != "production":
        return None

    public_paths = {"/", "/apidocs", "/apidocs/", "/apispec_1.json"}
    if (
        request.method == "OPTIONS"
        or request.path in public_paths
        or request.path.startswith("/apidocs/")
        or request.path.startswith("/flasgger_static/")
    ):
        return None

    return require_api_key()

# Inicialização de Clientes
redis_client = None
bq_client = None
DB_SCHEMA = {}
DB_REQUIRED_FIELDS = {}
DB_FIELD_TYPES = {}

try:
    google_credentials, detected_project = google.auth.default(
        scopes=[
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
    )
    bq_client = bigquery.Client(
        project=PROJECT_ID or detected_project,
        credentials=google_credentials,
    )
    if REDIS_URL:
        redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    else:
        redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD or None,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    redis_client.ping()
    logger.info(f"{APP_NAME} v{__version__} - Clientes GCP inicializados.")
except Exception as e:
    logger.error(f"Erro ao inicializar clientes (verifique BigQuery/Redis): {e}")


def fetch_schema_from_db():
    """Busca o schema da tabela de regras diretamente no BigQuery."""
    if bq_client is None:
        logger.warning("Schema não carregado: cliente BigQuery indisponível.")
        return {}

    try:
        table = bq_client.get_table(BQ_TABLE)
        return {
            field.name: {
                "type": field.field_type,
                "mode": field.mode,
                "description": field.description,
            }
            for field in table.schema
        }
    except Exception as error:
        logger.error("Erro ao buscar schema do BigQuery: %s", error)
        return {}


DB_SCHEMA = fetch_schema_from_db()
DB_REQUIRED_FIELDS = {
    name: definition
    for name, definition in DB_SCHEMA.items()
    if definition.get("mode") == "REQUIRED"
}
DB_FIELD_TYPES = {
    name: definition.get("type")
    for name, definition in DB_SCHEMA.items()
}

# Cache para Deduplicação (Layer 1) - TTL de 2 segundos
DEDUP_TTL = float(os.environ.get('DEDUP_TTL', 2.0))
DEDUP_MAXSIZE = int(os.environ.get('DEDUP_MAXSIZE', 1000))
dedup_cache = TTLCache(maxsize=DEDUP_MAXSIZE, ttl=DEDUP_TTL)
dedup_lock = threading.Lock()

# --- FUNÇÕES AUXILIARES ---
def _safe_key(value):
    return quote_plus(str(value))


def _slugify(*parts):
    raw = "_".join([str(p) for p in parts if p is not None and p != ""])
    slug = re.sub(r'[^A-Za-z0-9]+', '_', raw)
    slug = re.sub(r'_+', '_', slug).strip('_')
    return slug[:200]


def _rule_key(event_id):
    return f"{REDIS_PREFIX}:rule:{event_id}"


def _meta_key(event_id):
    return f"{REDIS_PREFIX}:meta:{event_id}"


def _event_index_key(event_name):
    return f"{REDIS_PREFIX}:event_name:{_safe_key(event_name).lower()}"


def _map_index_key(map_id, map_version):
    return f"{REDIS_PREFIX}:map:{_safe_key(map_id)}:{_safe_key(map_version)}"


def _redis_available():
    return redis_client is not None

def fetch_map_from_bigquery(map_id=None, map_version=None):
    """
    Vai ao BigQuery, busca as regras e transforma em formato Hierárquico (JSON).
    Se map_id for fornecido, filtra por ele (ex: versão do app ou plataforma).
    """

    if not map_id:
        raise ValueError("map_id é obrigatório para carregar um mapa específico!")

    # Obtém a versão mais recente do mapa mesmo se map_version não for declarado.
    map_version_query = "@map_version" if map_version is not None else f"(SELECT MAX(map_version) FROM `{BQ_TABLE}` WHERE map_id = @map_id)"
    
    query = f"""
        SELECT *
        FROM `{BQ_TABLE}`
        WHERE map_id = @map_id
        AND map_version = {map_version_query}
        LIMIT 500
    """

    query_params = [
        bigquery.ScalarQueryParameter("map_id", "STRING", str(map_id)),
        bigquery.ScalarQueryParameter("map_version", "STRING", str(map_version) if map_version is not None else None),
    ]
    job_config = bigquery.QueryJobConfig(query_parameters=query_params)

    query_job = bq_client.query(query, job_config=job_config)
    rows = list(query_job.result())

    # Transformação: cada linha é 'wide' — tem colunas de metadados + colunas de parâmetros.
    events_cache = {}
    # chaves de metadados que serão mantidas separadas no documento (não entram em `params`)
    meta_keys = {"map_id", "map_version", "event_name","event_id"}

    def _slugify(*parts):
        # cria uma chave legível a partir de partes, sanitizando caracteres
        raw = "_".join([str(p) for p in parts if p is not None and p != ""])
        slug = re.sub(r'[^A-Za-z0-9]+', '_', raw)
        slug = re.sub(r'_+', '_', slug).strip('_')
        return slug[:200]  # limite de comprimento

    for row in rows:
        # Converte Row para dict
        try:
            row_dict = dict(row)
        except Exception:
            # Fallback: se row não for mapeável diretamente, tente acessar atributos
            row_dict = {k: getattr(row, k, None) for k in dir(row) if not k.startswith("_")}

        map_id = row_dict.get('map_id')
        map_version = row_dict.get('map_version')
        evt = row_dict.get("event_name")
        event_id = row_dict.get("event_id")
        if not evt or not map_id or not map_version:
            logger.warning("Linha com metadados incompletos ignorada: %s", row_dict)
            continue

        # Remova apenas os metadados (map_id/map_version/event_name); mantenha page_path/title/section/label em params
        params = {k: v for k, v in row_dict.items() if k not in meta_keys and v is not None}

        # Define uma chave única concatenando os campos na ordem do schema.
        identifier_values = {
            field: row_dict[field]
            for field in DB_SCHEMA
            if field != 'event_id' and row_dict.get(field) is not None
        }
        doc_id = event_id or _slugify(*identifier_values.values())

        #Define o schema das coleções
        doc_body = {
            "metadata": {
                "map_id": map_id,
                "map_version": map_version
            },
            "event_name": evt,
            "params": params
        }

        events_cache[doc_id] = doc_body

    return events_cache

def update_redis_cache(events_data, map_id=None, map_version=None):
    """
    Grava os dados transformados no Redis.
    Se map_id e map_version forem fornecidos, limpa o índice antigo desse mapa.
    """
    if not _redis_available():
        raise RuntimeError("Redis indisponível")

    # Cria um pipeline para agrupar operações no Redis e executá-las em lote
    pipeline = redis_client.pipeline()

    # Se map_id e map_version foram informados, remove o índice anterior desse mapa
    if map_id and map_version:
        # Busca todos os event_id associados ao mapa anterior
        previous_event_ids = redis_client.smembers(_map_index_key(map_id, map_version))
        for event_id in previous_event_ids:
            # Remove a regra e os metadados de cada evento antigo
            pipeline.delete(_rule_key(event_id), _meta_key(event_id))
        # Remove também o índice do mapa
        pipeline.delete(_map_index_key(map_id, map_version))

    # Percorre todos os eventos recebidos para gravar no Redis
    for event_id, rules in events_data.items():
        # Extrai metadados do evento
        metadata = rules.get('metadata', {}) or {}
        event_name = rules.get('event_name')
        current_map_id = metadata.get('map_id')
        current_map_version = metadata.get('map_version')

        # Salva a regra completa do evento
        pipeline.set(_rule_key(event_id), json.dumps(rules, ensure_ascii=False))

        # Salva metadados separados para facilitar consultas futuras
        pipeline.set(
            _meta_key(event_id),
            json.dumps(
                {
                    "map_id": current_map_id,
                    "map_version": current_map_version,
                    "event_name": event_name,
                },
                ensure_ascii=False,
            ),
        )

        # Indexa o evento pelo nome para buscas rápidas
        if event_name:
            pipeline.sadd(_event_index_key(event_name), event_id)

        # Indexa o evento por mapa e versão
        if current_map_id is not None and current_map_version is not None:
            pipeline.sadd(_map_index_key(current_map_id, current_map_version), event_id)

    # Executa todas as operações acumuladas no Redis
    pipeline.execute()


def _load_rule_from_redis(event_id):
    if not _redis_available():
        return None

    raw_rule = redis_client.get(_rule_key(event_id))
    if not raw_rule:
        return None

    try:
        return json.loads(raw_rule)
    except json.JSONDecodeError:
        logger.warning("Regra inválida no Redis para event_id=%s", event_id)
        return None


def _get_candidate_rules_by_event_name(event_name, map_id=None, map_version=None):
    if not _redis_available():
        return []

    event_ids = list(redis_client.smembers(_event_index_key(event_name)))
    if not event_ids:
        return []

    pipeline = redis_client.pipeline()
    for event_id in event_ids:
        pipeline.get(_rule_key(event_id))

    raw_rules = pipeline.execute()
    rules = []
    for event_id, raw_rule in zip(event_ids, raw_rules):
        if not raw_rule:
            redis_client.srem(_event_index_key(event_name), event_id)
            continue
        try:
            rule = json.loads(raw_rule)
            rule_metadata = rule.get("metadata", {}) or {}
            if map_id is not None and str(rule_metadata.get("map_id")) != str(map_id):
                continue
            if map_version is not None and str(rule_metadata.get("map_version")) != str(map_version):
                continue
            rules.append(rule)
        except json.JSONDecodeError:
            logger.warning("Ignorando regra inválida no Redis: %s", event_id)
    return rules


def _ordered_schema_values(payload):
    """Obtém valores do payload respeitando a ordem declarada em DB_SCHEMA."""
    metadata = payload.get("metadata") or {}
    params = payload.get("params") or {}
    values = {}
    for field_name in DB_SCHEMA:
        for source in (payload, metadata, params):
            if field_name in source and source[field_name] is not None:
                values[field_name] = source[field_name]
                break
    return values


def _load_rules_by_event_ids(event_ids):
    """Carrega regras do Redis e ignora índices que apontam para regras removidas."""
    if not _redis_available() or not event_ids:
        return []

    event_ids = list(event_ids)
    raw_rules = redis_client.pipeline().mget([_rule_key(event_id) for event_id in event_ids]).execute()[0]
    rules = []
    for event_id, raw_rule in zip(event_ids, raw_rules):
        if not raw_rule:
            continue
        try:
            rule = json.loads(raw_rule)
            rule["event_id"] = event_id
            rules.append(rule)
        except json.JSONDecodeError:
            logger.warning("Ignorando regra inválida: event_id=%s", event_id)
    return rules


def _get_rules_by_map(map_id, map_version=None):
    """Busca todas as regras associadas a um mapa, opcionalmente por versão."""
    pattern = _map_index_key(map_id, map_version) if map_version else f"{REDIS_PREFIX}:map:{_safe_key(map_id)}:*"
    event_ids = set()
    for index_key in redis_client.scan_iter(match=pattern):
        event_ids.update(redis_client.smembers(index_key))
    return _load_rules_by_event_ids(event_ids)


def _get_rules_by_parameter(parameter_name, parameter_value):
    """Busca regras cujo parâmetro tenha exatamente o valor solicitado."""
    event_ids = []
    for rule_key in redis_client.scan_iter(match=f"{REDIS_PREFIX}:rule:*"):
        event_ids.append(rule_key.removeprefix(f"{REDIS_PREFIX}:rule:"))

    matching_rules = []
    for rule in _load_rules_by_event_ids(event_ids):
        params = rule.get("params", {}) or {}
        if str(params.get(parameter_name)) == parameter_value:
            matching_rules.append(rule)
    return matching_rules


def _matches_expected(expected_value, actual_value):
    if expected_value is None:
        return True

    expected_text = str(expected_value)
    actual_text = str(actual_value)

    if expected_text == actual_text:
        return True

    marker_positions = [idx for idx in [expected_text.find('%'), expected_text.find('{{')] if idx != -1]
    if marker_positions:
        prefix_end = min(marker_positions)
        return prefix_end == 0 or expected_text[:prefix_end] == actual_text[:prefix_end]

    return False
# --- FUNÇÕES DE VALIDAÇÃO (LAYERS) ---

def validate_deduplication(raw_payload, payload=None):
    """Layer 1: Verifica hash do payload para evitar duplicidade imediata.

    Suporte por 'session' / 'client': o cache usa uma chave composta por
    `client_id:event_hash` quando `client_id` for fornecido no `payload` ou no
    header `X-CLIENT-ID`. Caso contrário usa uma chave global.

    - `raw_payload`: string bruta do request (usada para o hash)
    - `payload`: dicionário JSON já parseado (opcional)
    """
    if not raw_payload:
        return None

    # calcula o hash do payload
    event_hash = hashlib.md5(raw_payload.encode('utf-8')).hexdigest()

    # tenta extrair client_id do payload ou do header
    client_id = None
    if payload and isinstance(payload, dict):
        client_id = payload.get('client_id')
    if not client_id:
        client_id = request.headers.get('X-CLIENT-ID')

    key = f"{client_id or 'global'}:{event_hash}"

    # Acesso ao cache protegido por lock (cachetools.TTLCache não é thread-safe)
    with dedup_lock:
        if key in dedup_cache:
            return {
                "status": "ERROR",
                "layer": "Deduplication",
                "message": "Evento duplicado detectado em curto intervalo."
            }
        dedup_cache[key] = True

    return None

def validate_taxonomy(payload):
    """Layer 2: Verifica padrões de nomenclatura (snake_case, prefixos).

    Recebe o `payload` completo (contendo `event_name` e `params`).
    """
    event_name = payload.get('event_name')
    params = payload.get('params', {}) or {}
    issues = []

    if not event_name or not re.match(r'^[a-z0-9_]+$', event_name):
        issues.append(f"Nome do evento '{event_name}' deve ser snake_case.")

    for param in params.keys():
        if param.startswith(('ga_', 'google_', 'firebase_')):
            issues.append(f"Parâmetro '{param}' usa prefixo reservado proibido.")
        if not re.match(r'^[a-z0-9_]+$', param):
            issues.append(f"Parâmetro '{param}' deve ser snake_case.")

    if issues:
        return {"status": "ERROR", "layer": "Taxonomy", "issues": issues}
    return None

def validate_schema(payload):
    """Layer 3: Valida contra regras do mapa de coleta carregadas no Redis.

    Recebe o `payload` completo e usa `metadata` (se presente) para buscar o
    documento de forma precisa; caso contrário realiza uma busca baseada em
    `event_name` e possíveis campos presentes dentro de `params`.
    """
    if not _redis_available():
        return {"status": "SKIPPED", "message": "🔴 Redis indisponível (Erro de conexão)"}

    if next(redis_client.scan_iter(match=f"{REDIS_PREFIX}:rule:*", count=1), None) is None:
        return {
            "status": "SKIPPED",
            "layer": "Schema",
            "message": "Cache de eventos vazio. Carregue um mapa com /loadmap.",
        }

    try:
        map_id = payload.get('map_id')
        event_id = payload.get('event_id')
        event_name = payload.get('event_name')
        params = payload.get('params', {}) or {}
        
        doc_dict = None

        # 1. Busca exata por event_id (prioridade)
        if event_id:
            doc_dict = _load_rule_from_redis(event_id)
            if doc_dict:
                pass
            else:
                return {
                    "status": "WARNING",
                    "layer": "Schema",
                    "message": f"⚠️ event_id '{event_id}' não encontrado no cache de regras."
                }

        # 2. Busca pelo mapa: a versão é opcional, mas restringe a busca quando informada.
        elif map_id:
            schema_values = _ordered_schema_values(payload)
            map_id = schema_values.get('map_id')
            map_version = schema_values.get('map_version')

            if map_version:
                identifier_values = {
                    field: schema_values[field]
                    for field in DB_SCHEMA
                    if field not in {'event_id'} and schema_values.get(field) is not None
                }
                doc_id = _slugify(*identifier_values.values())
                doc_dict = _load_rule_from_redis(doc_id)
            else:
                docs = _get_candidate_rules_by_event_name(event_name, map_id=map_id)
                context_fields = [field for field in DB_SCHEMA if field not in {
                    'map_id', 'map_version', 'event_id'
                } and field in schema_values]
                matching_docs = []
                for candidate in docs:
                    candidate_params = candidate.get('params', {}) or {}
                    if all(_matches_expected(candidate_params.get(field), schema_values[field])
                           for field in context_fields):
                        matching_docs.append(candidate)
                doc_dict = matching_docs[0] if len(matching_docs) == 1 else None
            
            if not doc_dict:
                return {
                    "status": "WARNING",
                    "layer": "Schema",
                    "message": f"⚠️ Evento '{event_name}' não documentado para este contexto."
                }
        # 3.
        else:
            docs = _get_candidate_rules_by_event_name(event_name, map_id=payload.get('map_id'))
            if not docs:
                return {
                    "status": "WARNING",
                    "layer": "Schema",
                    "message": f"⚠️ Evento '{event_name}' não documentado no cache de regras."
                }
            # Busca aproximada: retorna opções possíveis para cada parâmetro divergente
            best_match = None
            max_matches = 0
            param_options = {k: set() for k in params.keys() if k != 'event_name'}
            for doc_data in docs:
                doc_params = doc_data.get('params', {})
                matches = 0
                for k, v in params.items():
                    if k == 'event_name':
                        continue
                    expected = doc_params.get(k)
                    actual = v
                    if expected is None:
                        continue
                    if _matches_expected(expected, actual):
                        matches += 1
                    else:
                        param_options[k].add(expected)
                if matches > max_matches:
                    max_matches = matches
                    best_match = doc_data
            doc_dict = best_match if best_match else docs[0]

            # Se houver divergências, retorna opções possíveis para cada parâmetro
            suggestions = {k: sorted(list(v)) for k, v in param_options.items() if v}
            if suggestions:
                return {
                    "status": "SUGGESTIONS",
                    "layer": "Schema",
                    "message": "Valores aproximados encontrados para parâmetros divergentes.",
                    "suggestions": suggestions
                }

        expected_params = doc_dict.get('params', {})
        issues = []

        for key, value in expected_params.items():
            if key not in params:
                issues.append(f"Parâmetro esperado ausente: {key}")
                continue

            # Comparar valores apenas se ambos existirem
            expected_value = expected_params.get(key)
            actual_value = params.get(key)
            
            # Só valida se o valor esperado não for None/vazio
            if expected_value is not None and expected_value != "" and actual_value != expected_value:
                issues.append(f"Valor de '{key}' inválido. Esperado: '{expected_value}', recebido: '{actual_value}'")

        if issues:
            return {"status": "ERROR", "layer": "Schema", "issues": issues}
        return None

    except Exception as e:
        logger.error(f"Erro ao ler Redis: {e}")
        return {"status": "ERROR", "layer": "Schema", "message": str(e)}

def validate_google_mp(payload):
    """Layer 4: Envia para Google Analytics Debug Protocol."""
    meas_id = payload.get('measurement_id')
    api_secret = payload.get('measurement_protocol_api_secret')
    
    if not meas_id or not api_secret:
        return {"status": "SKIPPED", "layer": "Google Protocol", "message": "Sem credenciais para o Measurement Protocol."}

    ga4_payload = {
        "client_id": payload.get("params", {}).get("client_id", "test_user"),
        "timestamp_micros": payload.get("timestamp_micros"),
        "validation_behavior": "ENFORCE_RECOMMENDATIONS",
        "events": [{
            "name": payload.get("event_name"),
            "params": payload.get("params", {})
        }]
    }
    # return {"status": "INFO", "layer": "Google Protocol", "message": str(ga4_payload)}

    url = f"https://www.google-analytics.com/debug/mp/collect?measurement_id={meas_id}&api_secret={api_secret}"
    
    try:
        response = requests.post(url, json=ga4_payload, timeout=3)
        # O endpoint de debug retorna 200 mesmo com erros de validação no corpo
        if response.status_code == 200:
            google_resp = response.json()
            validation_messages = google_resp.get('validationMessages', [])
            if validation_messages:
                return {
                    "status": "ERROR", 
                    "layer": "Google Protocol", 
                    "google_feedback": validation_messages
                }
        else:
             return {"status": "ERROR", "layer": "Google Protocol", "message": f"⚠️ sHTTP {response.status_code}"}
        
    except Exception as e:
        return {"status": "ERROR", "layer": "Google Protocol", "message": str(e)}

    return None

# --- ENDPOINTS ---

@app.route('/', methods=['GET'])
def health_check():
    """Verifica a disponibilidade da API e de suas dependências.
    ---
    tags: [System]
    produces: [application/json]
    responses:
        200:
            description: API e dependências disponíveis
            schema:
                type: object
                required: [status, version, app, docs, dependencies]
                properties:
                    status: {type: string, example: ONLINE}
                    version: {type: string, example: 0.0.2}
                    app: {type: string, example: Tagging Validation API}
                    docs: {type: string, example: /apidocs}
                    dependencies:
                        type: object
                        properties:
                            redis: {type: string, example: UP}
                            database: {type: string, example: UP}
        500:
            description: Erro interno ao verificar a disponibilidade da API
            schema:
                $ref: '#/definitions/ErrorResponse'
        503:
            description: Uma ou mais dependências estão indisponíveis
            schema:
                type: object
                required: [status, version, app, docs, dependencies]
                properties:
                    status: {type: string, example: DEGRADED}
                    version: {type: string, example: 0.0.2}
                    app: {type: string, example: Tagging Validation API}
                    docs: {type: string, example: /apidocs}
                    dependencies:
                        type: object
                        properties:
                            redis: {type: string, example: DOWN}
                            database: {type: string, example: UP}
                    errors:
                        type: object
                        additionalProperties: {type: string}
    """
    dependencies = {}
    errors = {}

    try:
        if not _redis_available():
            raise RuntimeError("Cliente Redis não inicializado")
        redis_client.ping()
        dependencies["redis"] = "UP"
    except Exception as error:
        dependencies["redis"] = "DOWN"
        errors["redis"] = str(error)

    try:
        if bq_client is None:
            raise RuntimeError("Cliente BigQuery não inicializado")
        bq_client.get_table(BQ_TABLE)
        dependencies["database"] = "UP"
    except Exception as error:
        dependencies["database"] = "DOWN"
        errors["database"] = str(error)

    response = {
        "status": "ONLINE" if not errors else "DEGRADED",
        "version": __version__,
        "app": APP_NAME,
        "docs": "/apidocs",
        "dependencies": dependencies,
    }

    if errors:
        response["errors"] = errors
        return jsonify(response), 503

    return jsonify(response), 200

@app.route('/loadmaps', methods=['POST'])
def loadmaps():
    """Carrega regras de vários mapas do BigQuery para o Redis.
    ---
    tags: [Cache]
    security: [{bearerAuth: []}]
    consumes: [application/json]
    parameters: [{in: body, name: body, required: true, schema: {type: array, items: {type: string}, example: ["00001", "00002"]}}]
    produces: [application/json]
    responses:
      200: {description: Todos os mapas foram carregados sem erros, schema: {$ref: '#/definitions/LoadMapsResponse'}}
      207: {description: Um ou mais mapas falharam durante o carregamento, schema: {$ref: '#/definitions/LoadMapsResponse'}}
      400: {description: O corpo deve ser um array não vazio de map_ids, schema: {$ref: '#/definitions/StatusErrorResponse'}}
      500: {description: Cliente BigQuery indisponível ou erro interno, schema: {$ref: '#/definitions/StatusErrorResponse'}}
    """
    if not bq_client:
        return jsonify({"error": "Cliente BigQuery não inicializado"}), 500
    
    # return jsonify({"status": "MOCK_SUCCESS", "message": "Função de refresh pronta para implementação"}), 200
        
    try:
        map_ids = request.get_json(silent=True)
        if not isinstance(map_ids, list) or not map_ids or any(not isinstance(map_id, str) or not map_id.strip() for map_id in map_ids):
            return jsonify({
                "message": "O corpo deve ser um array não vazio de map_ids, por exemplo: [\"00001\", \"00002\"].",
            }), 400

        results = []
        for map_id in map_ids:
            logger.info("Iniciando refresh do mapa. Map ID: %s", map_id)
            try:
                events_data = fetch_map_from_bigquery(map_id)
                if not events_data:
                    results.append({"map_id": map_id, "status": "EMPTY", "events": 0})
                    continue

                first_doc = next(iter(events_data.values()))
                map_version = first_doc.get('metadata', {}).get('map_version')
                update_redis_cache(events_data, map_id=map_id, map_version=map_version)
                results.append({
                    "map_id": map_id,
                    "map_version": map_version,
                    "status": "SUCCESS",
                    "events": len(events_data),
                })
            except Exception as error:
                logger.error("Erro no refresh do mapa %s: %s", map_id, error)
                results.append({"map_id": map_id, "status": "ERROR", "message": str(error)})

        has_errors = any(result["status"] == "ERROR" for result in results)
        has_success = any(result["status"] == "SUCCESS" for result in results)
        status = "PARTIAL" if has_errors and has_success else "ERROR" if has_errors else "SUCCESS"
        return jsonify({
            "status": status,
            "mode": "COLD_START_COMPLETE",
            "maps": results,
        }), 200 if not has_errors else 207

    except Exception as e:
        logger.error(f"Erro no refresh: {e}")
        return jsonify({"status": "ERROR", "message": str(e)}), 500

@app.route('/events', methods=['DELETE'])
def delete_events():
    """Limpa os eventos de todos os mapas em cache.
    ---
    tags: [Cache]
    security: [{bearerAuth: []}]
    consumes: [application/json]
    parameters: [{in: header, name: X-ADMIN-KEY, required: false, type: string, description: Chave administrativa quando ADMIN_KEY estiver configurada}, {in: body, name: body, required: true, schema: {type: object, required: [confirm], properties: {confirm: {type: boolean, example: true}, admin_key: {type: string, example: ""}}}}]
    produces: [application/json]
    responses:
      200: {description: Cache limpo com sucesso, schema: {$ref: '#/definitions/ClearCacheResponse'}}
      400: {description: JSON ausente ou operação não confirmada, schema: {$ref: '#/definitions/ErrorResponse'}}
      401: {description: Chave administrativa inválida, schema: {$ref: '#/definitions/ErrorResponse'}}
      500: {description: Redis indisponível ou erro interno, schema: {$ref: '#/definitions/StatusErrorResponse'}}
    """
    if not _redis_available():
        return jsonify({"error": "Redis indisponível"}), 500

    try:
        admin_key = os.environ.get('ADMIN_KEY')
        if admin_key:
            provided = request.headers.get('X-ADMIN-KEY') or (request.get_json(silent=True) or {}).get('admin_key')
            if provided != admin_key:
                return jsonify({"error": "Unauthorized"}), 401

        if not request.is_json:
            return jsonify({"error": "JSON required"}), 400

        payload = request.get_json()
        if not payload or not payload.get('confirm'):
            return jsonify({"error": "Operation not confirmed. Send {\"confirm\": true}"}), 400

        deleted = 0
        keys = list(redis_client.scan_iter(match=f"{REDIS_PREFIX}:*"))
        for start in range(0, len(keys), 500):
            chunk = keys[start:start + 500]
            if chunk:
                deleted += redis_client.delete(*chunk)

        return jsonify({"status": "SUCCESS", "deleted": deleted}), 200

    except Exception as e:
        logger.error(f"Erro ao limpar cache: {e}", exc_info=True)
        return jsonify({"status": "ERROR", "message": str(e)}), 500

@app.route('/event/<event_id>', methods=['GET'])
def get_event(event_id):
    """Obtém um evento pelo event_id.
    ---
    tags: [Events]
    security: [{bearerAuth: []}]
    parameters: [{name: event_id, in: path, required: true, type: string, example: 00002_20260105_click_home_botao or 65831d59d8fa2329835554070e84ea9a}]
    produces: [application/json]
    responses:
      200: {description: Evento encontrado, schema: {$ref: '#/definitions/EventRule'}}
      404: {description: Evento não encontrado, schema: {$ref: '#/definitions/ErrorResponse'}}
      500: {description: Redis indisponível, schema: {$ref: '#/definitions/ErrorResponse'}}
    """

    if not _redis_available():
        return jsonify({"error": "Redis indisponível"}), 500

    rule = _load_rule_from_redis(event_id)
    if not rule:
        return jsonify({"error": "Evento não encontrado.", "details": [{"event_id": event_id}]}), 404

    rule["event_id"] = event_id
    return jsonify(rule), 200

@app.route('/map', methods=['GET'])
def get_map_events():
    """Obtém todas os eventos de um mapa.
    ---
    tags: [Events]
    security: [{bearerAuth: []}]
    parameters: [{name: map_id, in: query, required: true, type: string, example: "00002"}, {name: map_version, in: query, required: false, type: string}]
    produces: [application/json]
    responses:
        200:
            description: Eventos encontrados no mapa
            schema:
                $ref: '#/definitions/MapEventsResponse'
        400:
            description: map_id obrigatório
            schema:
                $ref: '#/definitions/ErrorResponse'
        500:
            description: Redis indisponível
            schema:
                $ref: '#/definitions/ErrorResponse'
    """
    if not _redis_available():
        return jsonify({"error": "Redis indisponível"}), 500

    map_id = request.args.get("map_id")
    map_version = request.args.get("map_version")
    if not map_id:
        return jsonify({"error": "Informe o parâmetro map_id."}), 400

    events = _get_rules_by_map(map_id, map_version)
    return jsonify({
        "map_id": map_id,
        "map_version": map_version,
        "count": len(events),
        "events": events,
    }), 200

@app.route('/events', methods=['GET'])
def get_events_by_parameter():
    """Obtém eventos filtrando um parâmetro de query string.
    ---
    tags: [Events]
    security: [{bearerAuth: []}]
    parameters: [{name: event_action, in: query, required: true, type: string, example: click}]
    produces: [application/json]
    responses:
        200:
            description: Eventos encontrados
            schema:
                $ref: '#/definitions/FilteredEventsResponse'
        400:
            description: Parâmetro ausente ou mais de um filtro informado
            schema:
                $ref: '#/definitions/ErrorResponse'
        500:
            description: Redis indisponível
            schema:
                $ref: '#/definitions/ErrorResponse'
    """
    if not _redis_available():
        return jsonify({"error": "Redis indisponível"}), 500

    filters = list(request.args.items())
    if not filters:
        return jsonify({"error": "Informe um parâmetro de filtro, por exemplo event_action=click."}), 400
    if len(filters) > 1:
        return jsonify({"error": "Informe apenas um parâmetro de filtro por consulta."}), 400

    parameter_name, parameter_value = filters[0]
    events = _get_rules_by_parameter(parameter_name, parameter_value)
    return jsonify({
        "parameter": parameter_name,
        "value": parameter_value,
        "count": len(events),
        "events": events,
    }), 200



def _parse_validation_request():
    if not request.is_json:
        return None, None, (jsonify({"error": "Content-Type deve ser application/json."}), 400)

    raw_payload = request.data.decode('utf-8')
    payload = request.get_json(silent=True)
    format_errors = []

    if payload is None:
        format_errors.append("O corpo deve conter um JSON válido.")
    elif not isinstance(payload, dict):
        format_errors.append("O payload deve ser um objeto JSON.")
    else:
        event_name = payload.get('event_name')
        map_id = payload.get('map_id')
        params = payload.get('params', {})

        if not isinstance(event_name, str) or not event_name.strip():
            format_errors.append("'event_name' é obrigatório e deve ser uma string não vazia.")
        if not isinstance(map_id, str) or not map_id.strip():
            format_errors.append("'map_id' é obrigatório e deve ser uma string não vazia.")
        if not isinstance(params, dict):
            format_errors.append("'params' deve ser um objeto JSON.")

    if format_errors:
        return None, None, (jsonify({
            "error": "Payload inválido.",
            "details": format_errors,
        }), 400)

    return payload, raw_payload, format_errors


VALIDATION_LAYER_NAMES = (
    "deduplication",
    "taxonomy",
    "schema",
    "google_mp",
)
VALIDATION_LAYERS = set(VALIDATION_LAYER_NAMES)


def _get_requested_layers(payload):
    layers = payload.get("layers")
    if layers is None:
        return list(VALIDATION_LAYER_NAMES), None

    if not isinstance(layers, list) or any(not isinstance(layer, str) for layer in layers):
        return None, "'layers' deve ser um array de strings."

    if not layers:
        return list(VALIDATION_LAYER_NAMES), None

    unexpected_layers = sorted(set(layers) - VALIDATION_LAYERS)
    if unexpected_layers:
        return None, (
            "Camadas inesperadas em 'layers': "
            + ", ".join(unexpected_layers)
            + ". Valores permitidos: "
            + ", ".join(sorted(VALIDATION_LAYERS))
            + "."
        )

    return layers, None


def _build_layer_report(payload, layer_key, validation_result, invalid_statuses):
    status = validation_result.get('status') if validation_result else "OK"
    return {
        "event": payload['event_name'],
        "valid": status not in invalid_statuses,
        "layers": {
            layer_key: validation_result or {"status": "OK"}
        }
    }


def _layer_response(payload, layer_key, validation_result, invalid_statuses):
    report = _build_layer_report(payload, layer_key, validation_result, invalid_statuses)
    return jsonify({
        "summary": _format_readable_report(report),
        "details": report,
    }), 200


@app.route('/validate-deduplication', methods=['POST'])
def post_validate_deduplication():
    """Valida se o evento foi enviado com duplicação.
    ---
    tags: [Validation]
    security: [{bearerAuth: []}]
    consumes: [application/json]
    parameters: [{in: body, name: body, required: true, schema: {type: object, properties: {event_name: {type: string, example: click}, map_id: {type: string, example: "00001"}, measurement_id: {type: string, example: G-NF7LZK2M10}, params: {type: object, example: {page_path: "/", title: "Página inicial", section: header, component: botao, label: "botao:explorar"}}}, example: {event_name: click, map_id: "00001", measurement_id: G-NF7LZK2M10, params: {page_path: "/", title: "Página inicial", section: header, component: botao, label: "botao:explorar"}}}}]
    produces: [application/json]
    responses:
        200:
            description: Relatório de validação gerado
            schema:
                $ref: '#/definitions/ValidationResponse'
        400:
            description: Payload JSON inválido ou fora do formato esperado
            schema:
                $ref: '#/definitions/ErrorResponse'
        500:
            description: Erro interno durante a validação
            schema:
                $ref: '#/definitions/ErrorResponse'
    """
    try:
        payload, raw_payload, error_response = _parse_validation_request()
        if error_response:
            return error_response

        return _layer_response(
            payload,
            "deduplication",
            validate_deduplication(raw_payload, payload),
            {"ERROR"},
        )

    except Exception as e:
        logger.error(f"Erro Fatal no validate_full: {e}", exc_info=True)
        return jsonify({"error": "Erro interno no servidor", "details": str(e)}), 500


@app.route('/validate-taxonomy', methods=['POST'])
def post_validate_taxonomy():
    """Valida isoladamente a nomenclatura do evento e seus parâmetros.
        ---
        tags: [Validation]
        security: [{bearerAuth: []}]
        consumes: [application/json]
        parameters: [{in: body, name: body, required: true, schema: {type: object, required: [event_name, map_id], properties: {event_name: {type: string, example: page_view}, map_id: {type: string, example: "00001"}, params: {type: object, additionalProperties: true, example: {page_path: "/home"}}}}}]
        produces: [application/json]
        responses:
            200:
                description: Relatório da validação de taxonomia
                schema: {$ref: '#/definitions/ValidationResponse'}
            400:
                description: Payload JSON inválido ou fora do formato esperado
                schema: {$ref: '#/definitions/ErrorResponse'}
            500:
                description: Erro interno durante a validação
                schema: {$ref: '#/definitions/ErrorResponse'}
        """
    try:
        payload, _, error_response = _parse_validation_request()
        if error_response:
            return error_response
        return _layer_response(payload, "taxonomy", validate_taxonomy(payload), {"ERROR"})
    except Exception as e:
        logger.error("Erro ao validar taxonomia: %s", e, exc_info=True)
        return jsonify({"error": "Erro interno no servidor", "details": str(e)}), 500


@app.route('/validate-schema', methods=['POST'])
def post_validate_schema():
    """Valida isoladamente o evento contra o mapa carregado no Redis.
        ---
        tags: [Validation]
        security: [{bearerAuth: []}]
        consumes: [application/json]
        parameters: [{in: body, name: body, required: true, schema: {type: object, required: [event_name, map_id], properties: {event_name: {type: string, example: page_view}, map_id: {type: string, example: "00001"}, map_version: {type: string, example: "1"}, event_id: {type: string, example: 00001_page_view_home}, params: {type: object, additionalProperties: true, example: {page_path: "/home"}}}}}]
        produces: [application/json]
        responses:
            200:
                description: Relatório da validação contra o schema
                schema: {$ref: '#/definitions/ValidationResponse'}
            400:
                description: Payload JSON inválido ou fora do formato esperado
                schema: {$ref: '#/definitions/ErrorResponse'}
            500:
                description: Erro interno durante a validação
                schema: {$ref: '#/definitions/ErrorResponse'}
        """
    try:
        payload, _, error_response = _parse_validation_request()
        if error_response:
            return error_response
        return _layer_response(payload, "schema", validate_schema(payload), {"ERROR", "WARNING"})
    except Exception as e:
        logger.error("Erro ao validar schema: %s", e, exc_info=True)
        return jsonify({"error": "Erro interno no servidor", "details": str(e)}), 500


@app.route('/validate-google-mp', methods=['POST'])
def post_validate_google_mp():
    """Valida isoladamente o evento contra o Google Measurement Protocol.
        ---
        tags: [Validation]
        security: [{bearerAuth: []}]
        consumes: [application/json]
        parameters: [{in: body, name: body, required: true, schema: {type: object, required: [event_name, map_id, measurement_id, measurement_protocol_api_secret, client_id], properties: {event_name: {type: string, example: page_view}, map_id: {type: string, example: "00001"}, measurement_id: {type: string, example: G-NF7LZK2M10}, measurement_protocol_api_secret: {type: string, example: secret}, params: {type: object, additionalProperties: true, example: {page_location: "https://example.com/home", client_id: "123456789.123456789"}}}}}]
        produces: [application/json]
        responses:
            200:
                description: Relatório da validação no Google Measurement Protocol
                schema: {$ref: '#/definitions/ValidationResponse'}
            400:
                description: Payload JSON inválido ou fora do formato esperado
                schema: {$ref: '#/definitions/ErrorResponse'}
            500:
                description: Erro interno durante a validação
                schema: {$ref: '#/definitions/ErrorResponse'}
        """
    try:
        payload, _, error_response = _parse_validation_request()
        if error_response:
            return error_response
        return _layer_response(payload, "google_mp", validate_google_mp(payload), {"ERROR"})
    except Exception as e:
        logger.error("Erro ao validar Google Measurement Protocol: %s", e, exc_info=True)
        return jsonify({"error": "Erro interno no servidor", "details": str(e)}), 500

@app.route('/validate', methods=['POST'])
def validate():
    """Valida um evento em 4 camadas: deduplicação, taxonomia, schema e Google MP.

    Utilize o parâmetro `layers` com os valores `deduplication`, `taxonomy`,
    `schema` e `google_mp` para habilitar uma ou mais camadas de validação.
    Se `layers` não for definido ou for vazio, o endpoint validará todas as camadas.
    ---
    tags: [Validation]
    security: [{bearerAuth: []}]
    consumes: [application/json]
    parameters: [{in: body, name: body, required: true, schema: {type: object, properties: {event_name: {type: string, example: click}, map_id: {type: string, example: "00001"}, layers: {type: array, items: {type: string, enum: [deduplication, taxonomy, schema, google_mp]}, example: [taxonomy, schema]}, measurement_id: {type: string, example: G-NF7LZK2M10}, params: {type: object, example: {page_path: "/", title: "Página inicial", section: header, component: botao, label: "botao:explorar"}}}, example: {event_name: click, map_id: "00001", layers: [taxonomy, schema], measurement_id: G-NF7LZK2M10, params: {page_path: "/", title: "Página inicial", section: header, component: botao, label: "botao:explorar"}}}}]
    produces: [application/json]
    responses:
        200:
            description: Relatório de validação gerado
            schema:
                $ref: '#/definitions/ValidationResponse'
        400:
            description: Payload JSON inválido ou fora do formato esperado
            schema:
                $ref: '#/definitions/ErrorResponse'
        500:
            description: Erro interno durante a validação
            schema:
                $ref: '#/definitions/ErrorResponse'
    """
    try:
        payload, raw_payload, error_response = _parse_validation_request()
        if error_response:
            return error_response

        requested_layers, layers_error = _get_requested_layers(payload)
        if layers_error:
            return jsonify({
                "error": "Parâmetro 'layers' inválido.",
                "details": [layers_error],
            }), 400

        layer_validators = {
            "deduplication": lambda: validate_deduplication(raw_payload, payload),
            "taxonomy": lambda: validate_taxonomy(payload),
            "schema": lambda: validate_schema(payload),
            "google_mp": lambda: validate_google_mp(payload),
        }
        layer_results = {
            layer_key: layer_validators[layer_key]()
            for layer_key in requested_layers
        }
        invalid_statuses = {
            "deduplication": {"ERROR"},
            "taxonomy": {"ERROR"},
            "schema": {"ERROR", "WARNING"},
            "google_mp": {"ERROR"},
        }

        report = {
            "event": payload['event_name'],
            "valid": all(
                (result or {}).get("status", "OK") not in invalid_statuses[layer_key]
                for layer_key, result in layer_results.items()
            ),
            "layers": {
                layer_key: result or {"status": "OK"}
                for layer_key, result in layer_results.items()
            },
        }

        return jsonify({
            "summary": _format_readable_report(report),
            "details": report,
        }), 200

    except Exception as e:
        logger.error(f"Erro Fatal no validate_full: {e}", exc_info=True)
        return jsonify({"error": "Erro interno no servidor", "details": str(e)}), 500

def _format_readable_report(report):
    """Converte o relatório de validação em texto amigável"""
    lines = []
    
    # Cabeçalho
    status_emoji = "🟩" if report["valid"] else "🟥"
    lines.append(f"{status_emoji} RESULTADO DA VALIDAÇÃO: {'APROVADO' if report['valid'] else 'REPROVADO'} {status_emoji}")
    lines.append(f"\nEVENTO: {report['event']}")
    lines.append("")
    
    # Camadas
    layers = report.get("layers", {})
    layer_names = {
        "deduplication": "1️⃣ DEDUPLICAÇÃO",
        "taxonomy": "2️⃣ TAXONOMIA",
        "schema": "3️⃣ SCHEMA",
        "google_mp": "4️⃣ GOOGLE MEASUREMENT PROTOCOL"
    }
    
    for layer_key, layer_title in layer_names.items():
        if layer_key not in layers:
            continue
            
        layer_data = layers[layer_key]
        status = layer_data.get("status", "UNKNOWN")
        
        if status == "OK":
            lines.append(f"✅ {layer_title}")
        elif status == "ERROR":
            lines.append(f"❌ {layer_title}")
            
            # Mostrar detalhes do erro
            if "issues" in layer_data:
                for issue in layer_data["issues"]:
                    lines.append(f"• {issue}")
            elif "message" in layer_data:
                lines.append(f"• {layer_data['message']}")
            elif "google_feedback" in layer_data:
                lines.append(f"• Feedback do Google:")
                for msg in layer_data["google_feedback"]:
                    lines.append(f" - {msg}")
                    
        elif status == "WARNING":
            lines.append(f"{layer_title}: ⚠️ Aviso")
            if "message" in layer_data:
                lines.append(f"• {layer_data['message']}")
                
        elif status == "SKIPPED":
            lines.append(f"{layer_title}: ⏭️ Pulado")
            if "message" in layer_data:
                lines.append(f"• {layer_data['message']}")
        
        lines.append("")
    
    return "\n".join(lines)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host="0.0.0.0", port=port)