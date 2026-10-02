import os
import logging
import json
import hashlib
import time
import requests
import re
import secrets
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from flasgger import Swagger
from config import __version__, APP_NAME, APP_DESCRIPTION
import database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)
FLASK_ENV = os.environ.get("FLASK_ENV", "development")
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")
allowed_origins = [origin.strip() for origin in CORS_ORIGINS.split(",")] if FLASK_ENV == "production" and CORS_ORIGINS != "*" else "*"
CORS(app, resources={
    r"/*": {
        "origins": allowed_origins,
        "methods": ["GET", "POST", "OPTIONS", "DELETE"],
        "allow_headers": ["Content-Type", "Authorization", "X-CLIENT-ID", "X-ADMIN-KEY", "X-MEASUREMENT-PROTOCOL-API-SECRET"],
        "expose_headers": ["X-Response-Time"],
    }
})


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
API_KEY = os.environ.get("API_KEY")


def require_api_key():
    if not API_KEY:
        return jsonify({"error": "Autenticação da API não configurada."}), 500
    authorization = request.headers.get("Authorization", "").strip()
    scheme, separator, provided_key = authorization.partition(" ")
    if separator and (scheme.lower() != "bearer" or not provided_key.strip()):
        return jsonify({"error": "Authorization Bearer obrigatório."}), 401
    provided_key = provided_key if separator else authorization
    if not provided_key or not secrets.compare_digest(provided_key, API_KEY):
        return jsonify({"error": "Unauthorized"}), 401
    return None


@app.before_request
def authenticate_api_request():
    if FLASK_ENV != "production":
        return None
    public_paths = {"/", "/apidocs", "/apidocs/", "/apispec_1.json"}
    if request.method == "OPTIONS" or request.path in public_paths or request.path.startswith(("/apidocs/", "/flasgger_static/")):
        return None
    return require_api_key()


# Campos usados para localizar o contexto de uma regra dentro do JSONB.
DB_SCHEMA = {
    "map_version": {"type": "STRING", "mode": "REQUIRED"},
    "map_id": {"type": "STRING", "mode": "REQUIRED"},
    "event_name": {"type": "STRING", "mode": "REQUIRED"},
    "page_path": {"type": "STRING", "mode": "NULLABLE"},
    "title": {"type": "STRING", "mode": "NULLABLE"},
    "section": {"type": "STRING", "mode": "NULLABLE"},
    "component": {"type": "STRING", "mode": "NULLABLE"},
    "label": {"type": "STRING", "mode": "NULLABLE"},
    "outbound": {"type": "BOOLEAN", "mode": "NULLABLE"},
    "event_id": {"type": "STRING", "mode": "REQUIRED"},
}

DEDUP_TTL = float(os.environ.get("DEDUP_TTL", 2.0))
dedup_cache = {}
dedup_lock = threading.Lock()
try:
    database.initialize()
    logger.info("%s v%s - PostgreSQL inicializado.", APP_NAME, __version__)
except Exception as error:
    logger.error("PostgreSQL indisponível na inicialização: %s", error)


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


def _database_available():
    return bool(database.DATABASE_URL)


def replace_map(events_data, map_id=None, map_version=None):
    if not map_id or not map_version:
        raise ValueError("map_id e map_version são obrigatórios")
    database.replace_map(map_id, map_version, events_data)


def _load_rule(event_id, map_id=None):
    return database.get_event(event_id, map_id=map_id)


def _get_candidate_rules_by_event_name(event_name, map_id=None, map_version=None):
    return database.find_by_event_name(event_name, map_id=map_id, map_version=map_version)


def _get_rules_by_map(map_id, map_version=None):
    return database.get_map(map_id, map_version)


def _get_rules_by_parameter(parameter_name, parameter_value):
    return database.find_by_parameter(parameter_name, parameter_value)


def _matches_expected(expected_value, actual_value):
    if expected_value is None:
        return True

    expected_text = str(expected_value)
    actual_text = str(actual_value)

    if expected_text == actual_text:
        return True

    if "%" in expected_text:
        pattern = ".+".join(
            re.escape(part) for part in expected_text.split("%")
        )
        return re.fullmatch(pattern, actual_text) is not None

    marker_positions = [idx for idx in [expected_text.find('{{'), expected_text.find('}}')] if idx != -1]
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

    now = time.monotonic()
    with dedup_lock:
        expired_keys = [cache_key for cache_key, expires_at in dedup_cache.items() if expires_at <= now]
        for expired_key in expired_keys:
            del dedup_cache[expired_key]
        first_request = key not in dedup_cache
        if first_request:
            dedup_cache[key] = now + DEDUP_TTL

    if not first_request:
        return {
            "status": "ERROR",
            "layer": "Deduplication",
            "message": "Evento duplicado detectado em curto intervalo."
        }

    return None

def validate_taxonomy(payload):
    """Layer 2: Valida o evento contra as regras da tabela map_taxonomy.

    Recebe o `payload` completo (contendo `event_name` e `params`).
    """
    event_name = payload.get('event_name')
    params = payload.get('params', {}) or {}
    issues = []

    try:
        taxonomy = {rule["param_name"]: rule for rule in database.taxonomy_rules()}
    except Exception as error:
        logger.error("Erro ao carregar map_taxonomy: %s", error)
        return {"status": "ERROR", "layer": "Taxonomy", "message": str(error)}

    values = {**payload, **params}
    for field_name, rule in taxonomy.items():
        value = values.get(field_name)
        if rule["required"] and (value is None or str(value).strip() == ""):
            issues.append(rule["pattern_error"] or f"'{field_name}' é obrigatório.")
            continue
        pattern_value = str(value).lower() if isinstance(value, bool) else str(value)
        pattern_flags = re.IGNORECASE if isinstance(value, bool) else 0
        if value is not None and rule["pattern"] and not re.fullmatch(
            rule["pattern"], pattern_value, flags=pattern_flags
        ):
            issues.append(rule["pattern_error"] or f"'{field_name}' possui formato inválido.")
            # issues.append(f"{rule['pattern']} {str(value)} possui formato inválido.")

    for param in params.keys():
        if param.startswith(('ga_', 'google_', 'firebase_')):
            issues.append(f"Parâmetro '{param}' usa prefixo reservado proibido.")
        if not re.match(r'^[a-z0-9_]+$', param):
            issues.append(f"Parâmetro '{param}' deve ser snake_case.")

    if issues:
        return {"status": "ERROR", "layer": "Taxonomy", "issues": issues}
    return None

def validate_schema(payload):
    """Layer 3: Valida contra regras do mapa de coleta no PostgreSQL.

    Recebe o `payload` completo e usa `metadata` (se presente) para buscar o
    documento de forma precisa; caso contrário realiza uma busca baseada em
    `event_name` e possíveis campos presentes dentro de `params`.
    """
    if not _database_available():
        return {"status": "SKIPPED", "message": "PostgreSQL indisponível (erro de conexão)"}

    try:
        map_id = payload.get('map_id')
        event_id = payload.get('event_id')
        event_name = payload.get('event_name')
        params = payload.get('params', {}) or {}
        
        doc_dict = None

        # 1. Busca exata por event_id (prioridade)
        if event_id:
            doc_dict = _load_rule(event_id, map_id=payload.get('map_id'))
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
                candidates = _get_candidate_rules_by_event_name(
                    event_name,
                    map_id=map_id,
                    map_version=map_version,
                )
                context_fields = [
                    field for field in DB_SCHEMA
                    if field not in {'map_id', 'map_version', 'event_id'}
                    and field in schema_values
                ]
                matching_docs = []
                for candidate in candidates:
                    candidate_params = candidate.get('params', {}) or {}
                    if all(_matches_expected(candidate_params.get(field), schema_values[field])
                           for field in context_fields):
                        matching_docs.append(candidate)
                doc_dict = matching_docs[0] if len(matching_docs) == 1 else None
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
                    "message": f"Evento '{event_name}' não documentado neste mapa."
                }
        # 3.
        else:
            docs = _get_candidate_rules_by_event_name(event_name, map_id=payload.get('map_id'))
            if not docs:
                return {
                    "status": "WARNING",
                    "layer": "Schema",
                    "message": f"Evento '{event_name}' não documentado no cache de regras."
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
            
            # Só valida se o valor esperado não for None/vazio.
            if expected_value is not None and expected_value != "":
                matches = _matches_expected(expected_value, actual_value)

                if not matches:
                    issues.append(f"Valor de '{key}' inválido. Esperado: '{expected_value}', recebido: '{actual_value}'")

        if issues:
            return {"status": "ERROR", "layer": "Schema", "issues": issues}
        return None

    except Exception as e:
        logger.error("Erro ao ler PostgreSQL: %s", e)
        return {"status": "ERROR", "layer": "Schema", "message": str(e)}

def validate_google_mp(payload, api_secret=None):
    """Layer 4: Envia para Google Analytics Debug Protocol."""
    meas_id = payload.get('measurement_id')
    api_secret = api_secret or request.headers.get('X-MEASUREMENT-PROTOCOL-API-SECRET')
    
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
                            postgresql: {type: string, example: UP}
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
                            database: {type: string, example: UP}
                    errors:
                        type: object
                        additionalProperties: {type: string}
    """
    dependencies = {}
    errors = {}

    try:
        database.health_check()
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
    """Importa regras de vários mapas para o PostgreSQL.
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
    500: {description: PostgreSQL indisponível ou erro interno, schema: {$ref: '#/definitions/StatusErrorResponse'}}
    """
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, list) or not payload:
            return jsonify({
                "message": "O corpo deve ser um array de mapas com map_id, map_version e events.",
            }), 400

        results = []
        for map_data in payload:
            if not isinstance(map_data, dict):
                return jsonify({"message": "Cada mapa deve ser um objeto JSON."}), 400
            map_id = str(map_data.get("map_id", "")).strip()
            map_version = str(map_data.get("map_version", "")).strip()
            events = map_data.get("events")
            if not map_id or not map_version or not isinstance(events, list):
                return jsonify({
                    "message": "Cada mapa precisa de map_id, map_version e events (array).",
                }), 400
            logger.info("Iniciando refresh do mapa. Map ID: %s", map_id)
            try:
                events_data = {}
                for event in events:
                    if not isinstance(event, dict) or not event.get("event_id") or not event.get("event_name"):
                        raise ValueError("Cada evento precisa de event_id e event_name")
                    events_data[str(event["event_id"])] = event
                if not events_data:
                    results.append({"map_id": map_id, "status": "EMPTY", "events": 0})
                    continue

                replace_map(events_data, map_id=map_id, map_version=map_version)
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
    """Limpa os eventos de todos os mapas no PostgreSQL.
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
    500: {description: PostgreSQL indisponível ou erro interno, schema: {$ref: '#/definitions/StatusErrorResponse'}}
    """
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

        deleted = database.clear_events()

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
    500: {description: PostgreSQL indisponível, schema: {$ref: '#/definitions/ErrorResponse'}}
    """

    rule = _load_rule(event_id)
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
            description: PostgreSQL indisponível
            schema:
                $ref: '#/definitions/ErrorResponse'
    """
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
    parameters: [{name: event_name, in: query, required: true, type: string, example: click}]
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
            description: PostgreSQL indisponível
            schema:
                $ref: '#/definitions/ErrorResponse'
    """
    filters = list(request.args.items())
    if not filters:
        return jsonify({"error": "Informe um parâmetro de filtro, por exemplo event_name=click."}), 400
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
    status = validation_result.get('status') if validation_result else "OK"
    if status == "ERROR" and layer_key == "deduplication":
        http_status = 409
    elif status in invalid_statuses:
        http_status = 422
    else:
        http_status = 200
    return jsonify({
        "summary": _format_readable_report(report),
        "details": report,
    }), http_status


def _validation_http_status(layer_results, invalid_statuses):
    statuses = {
        layer_key: (result or {}).get("status", "OK")
        for layer_key, result in layer_results.items()
    }
    if statuses.get("deduplication") == "ERROR":
        return 409
    if any(status in invalid_statuses[layer_key] for layer_key, status in statuses.items()):
        return 422
    return 200


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
        409:
            description: Evento duplicado detectado
            schema:
                $ref: '#/definitions/ValidationResponse'
        422:
            description: Evento reprovado pela camada de validação
            schema:
                $ref: '#/definitions/ValidationResponse'
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
            422:
                description: Evento reprovado pela taxonomia
                schema: {$ref: '#/definitions/ValidationResponse'}
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
    """Valida isoladamente o evento contra o mapa armazenado no PostgreSQL.
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
            422:
                description: Evento incompatível com o schema do mapa
                schema: {$ref: '#/definitions/ValidationResponse'}
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
        O segredo deve ser enviado no header X-MEASUREMENT-PROTOCOL-API-SECRET.
        ---
        tags: [Validation]
        security: [{bearerAuth: []}]
        consumes: [application/json]
        parameters: [{in: header, name: X-MEASUREMENT-PROTOCOL-API-SECRET, required: false, type: string, description: Segredo do Google Measurement Protocol}, {in: body, name: body, required: true, schema: {type: object, required: [event_name, map_id, measurement_id], properties: {event_name: {type: string, example: page_view}, map_id: {type: string, example: "00001"}, measurement_id: {type: string, example: G-NF7LZK2M10}, params: {type: object, additionalProperties: true, example: {page_location: "https://example.com/home", client_id: "123456789.123456789"}}}}}]
        produces: [application/json]
        responses:
            200:
                description: Relatório da validação no Google Measurement Protocol
                schema: {$ref: '#/definitions/ValidationResponse'}
            400:
                description: Payload JSON inválido ou fora do formato esperado
                schema: {$ref: '#/definitions/ErrorResponse'}
            422:
                description: Evento reprovado pelo Google Measurement Protocol
                schema: {$ref: '#/definitions/ValidationResponse'}
            500:
                description: Erro interno durante a validação
                schema: {$ref: '#/definitions/ErrorResponse'}
        """
    try:
        payload, _, error_response = _parse_validation_request()
        if error_response:
            return error_response
        return _layer_response(
            payload,
            "google_mp",
            validate_google_mp(payload, request.headers.get('X-MEASUREMENT-PROTOCOL-API-SECRET')),
            {"ERROR"},
        )
    except Exception as e:
        logger.error("Erro ao validar Google Measurement Protocol: %s", e, exc_info=True)
        return jsonify({"error": "Erro interno no servidor", "details": str(e)}), 500

@app.route('/validate', methods=['POST'])
def validate():
    """Valida um evento em 4 camadas: deduplicação, taxonomia, schema e Google MP.

    Utilize o parâmetro `layers` com os valores `deduplication`, `taxonomy`,
    `schema` e `google_mp` para habilitar uma ou mais camadas de validação.
    Se `layers` não for definido ou for vazio, o endpoint validará todas as camadas.
    Quando `google_mp` estiver habilitado, envie o segredo no header
    `X-MEASUREMENT-PROTOCOL-API-SECRET`.
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
        409:
            description: Evento duplicado detectado
            schema:
                $ref: '#/definitions/ValidationResponse'
        422:
            description: Evento reprovado por uma camada de validação
            schema:
                $ref: '#/definitions/ValidationResponse'
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
            "google_mp": lambda: validate_google_mp(
                payload,
                request.headers.get('X-MEASUREMENT-PROTOCOL-API-SECRET'),
            ),
        }
        layer_results = {
            layer_key: layer_validators[layer_key]()
            for layer_key in requested_layers
        }
        invalid_statuses = {
            "deduplication": {"ERROR"},
            "taxonomy": {"ERROR"},
            "schema": {"ERROR", "WARNING", "SUGGESTIONS"},
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
        }), _validation_http_status(layer_results, invalid_statuses)

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
        "deduplication": "DEDUPLICAÇÃO",
        "taxonomy": "TAXONOMIA",
        "schema": "SCHEMA",
        "google_mp": "GOOGLE MEASUREMENT PROTOCOL"
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
            lines.append(f"⚠️ {layer_title}")
            if "message" in layer_data:
                lines.append(f"• {layer_data['message']}")
                
        elif status == "SKIPPED":
            lines.append(f"⏭️ {layer_title} (Pulado)")
            if "message" in layer_data:
                lines.append(f"• {layer_data['message']}")
        
        lines.append("")
    
    return "\n".join(lines)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host="0.0.0.0", port=port)