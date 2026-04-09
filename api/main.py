import os
import logging
import json
import hashlib
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
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-CLIENT-ID", "X-ADMIN-KEY"],
        "max_age": 86400  # Cache preflight por 24 horas
    }
})

logger.info(f"CORS configurado para {FLASK_ENV}: origins={allowed_origins}")

# Configuração do Swagger
app.config['SWAGGER'] = {
    'title': APP_NAME,
    'uiversion': 3,
    'description': APP_DESCRIPTION,
    'version': __version__
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

# Inicialização de Clientes (Robusta)
redis_client = None
bq_client = None

try:
    bq_client = bigquery.Client(project=PROJECT_ID)
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

    # Query SQL com parâmetros para evitar erro de aspas e injeção acidental
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
    # chaves que serão mantidas separadas no documento (não entram em `params`)
    meta_keys = {"map_id", "map_version", "event_name"}

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
        page_path = row_dict.get('page_path')
        title = row_dict.get('title')
        section = row_dict.get('section')
        label = row_dict.get('label')

        if not evt or not map_id or not map_version:
            logger.warning("Linha com metadados incompletos ignorada: %s", row_dict)
            continue

        # Remova apenas os metadados (map_id/map_version/event_name); mantenha page_path/title/section/label em params
        params = {k: v for k, v in row_dict.items() if k not in meta_keys and v is not None}

        # Definir uma chave única composta (usa alguns campos também presentes em params)
        if evt.lower() in ('page_view', 'pageview', 'page_view_event', 'screen_name'):
            unique_part = title or page_path or ''
        else:
            unique_part = (section or '') + '_' + (label or '')

        doc_id = _slugify(map_id, map_version, evt, page_path or '', unique_part)

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


def _get_candidate_rules_by_event_name(event_name):
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
            rules.append(json.loads(raw_rule))
        except json.JSONDecodeError:
            logger.warning("Ignorando regra inválida no Redis: %s", event_id)
    return rules


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

    try:
        event_id = payload.get('event_id')
        event_name = payload.get('event_name')
        params = payload.get('params', {}) or {}
        metadata = payload.get('metadata') or {}

        doc_dict = None

        # 1. Busca exata por event_id (prioridade máxima)
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

        # 2. Busca exata por map_id/map_version (mantém lógica anterior)
        elif metadata and metadata.get('map_id') and metadata.get('map_version'):
            map_id = metadata.get('map_id')
            map_version = metadata.get('map_version')
            page_path = metadata.get('page_path')
            title = metadata.get('title')
            section = metadata.get('section')
            label = metadata.get('label')
            outbound = metadata.get('outbound')

            def _slugify(*parts):
                raw = "_".join([str(p) for p in parts if p is not None and p != ""])
                slug = re.sub(r'[^A-Za-z0-9]+', '_', raw)
                slug = re.sub(r'_+', '_', slug).strip('_')
                return slug[:200]

            if event_name and event_name.lower() in ('page_view', 'pageview', 'page_view_event'):
                unique_part = title or page_path or ''
            else:
                unique_part = (section or '') + '_' + (label or '') + ('_' + (outbound or '') if outbound else '')

            doc_id = _slugify(map_id, map_version, event_name, page_path or '', unique_part)
            doc_dict = _load_rule_from_redis(doc_id)

            if doc_dict:
                pass
            else:
                return {
                    "status": "WARNING",
                    "layer": "Schema",
                    "message": f"⚠️ Evento '{event_name}' não documentado para este contexto."
                }
        else:
            docs = _get_candidate_rules_by_event_name(event_name)
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
    """Health Check com redirecionamento para Swagger"""
    return jsonify({
        "status": "ONLINE",
        "version": __version__,
        "app": APP_NAME,
        "docs": "/apidocs"
    }), 200

@app.route('/loadmap', methods=['POST'])
def refresh_rules():
    """
    Cold Start: Carrega regras do BigQuery para o Redis
    ---
    tags:
      - Carregar mapa
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            map_id:
              type: string
              description: "Versão específica do mapa a carregar (opcional)"
              example: "00001"
          example:
            map_id: "00001"
    responses:
      200:
        description: Cache atualizado com sucesso
        schema:
          type: object
          properties:
            status:
              type: string
              example: "SUCCESS"
            message:
              type: string
              example: "Cache atualizado com 42 eventos."
            mode:
              type: string
              example: "COLD_START_COMPLETE"
      404:
        description: Nenhum dado encontrado
      500:
        description: Erro na inicialização ou processamento
    """
    if not bq_client:
        return jsonify({"error": "Cliente BigQuery não inicializado"}), 500
    
    # return jsonify({"status": "MOCK_SUCCESS", "message": "Função de refresh pronta para implementação"}), 200
        
    try:
        payload = request.get_json() or {}
        map_id = payload.get('map_id') # Opcional: carregar versão específica

        logger.info(f"Iniciando refresh do mapa. Map ID: {map_id}")
        
        # 1. Busca e Transforma
        events_data = fetch_map_from_bigquery(map_id)
        
        if not events_data:
            return jsonify({"status": "EMPTY", "message": "Nenhum dado encontrado no BigQuery."}), 404

        # Extrai map_version do primeiro documento (todos têm a mesma versão após fetch)
        first_doc = next(iter(events_data.values()))
        map_version = payload.get('map_id') or first_doc.get('metadata', {}).get('map_version')

        # 2. Atualiza Cache (remove antigos do mesmo map_id/map_version e insere novos)
        update_redis_cache(events_data, map_id=map_id, map_version=map_version)
        
        return jsonify({
            "status": "SUCCESS", 
            "message": f"Cache atualizado com {len(events_data)} eventos.",
            "mode": "COLD_START_COMPLETE"
        }), 200

    except Exception as e:
        logger.error(f"Erro no refresh: {e}")
        return jsonify({"status": "ERROR", "message": str(e)}), 500

@app.route('/clear-cache', methods=['POST'])
def clear_cache():
    
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


@app.route('/validate', methods=['POST'])
def validate():
    """
    Validação Completa (4 Camadas)
    ---
    tags:
      - Validação
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - event_name
          properties:
            event_name:
              type: string
              example: "click"
            measurement_id:
              type: string
              example: "G-NF7LZK2M10"
            measurement_protocol_api_secret:
                type: string
                example: "xYZ123abcDEF456ghi789JKL"
            api_secret:
              type: string
              example: "7IrA3QyPTJaCUe1edtAh3w"
            params:
              type: object
              example:
                map_id: "00001"
                map_version: 20260105 
                page_path: "/"
                section: "header"
                label: "botao:seta-baixo"
    responses:
      200:
        description: Relatório de Validação
    """
    try:
        if not request.is_json:
            return jsonify({"error": "JSON required"}), 400

        raw_payload = request.data.decode('utf-8')
        payload = request.get_json()
        
        event_name = payload.get('event_name')
        params = payload.get('params', {})
        
        report = {
            "event": event_name,
            "valid": True,
            "layers": {}
        }

        # 1. Deduplicação
        dedup_res = validate_deduplication(raw_payload)
        if dedup_res:
            report["valid"] = False
            report["layers"]["deduplication"] = dedup_res
        else:
            report["layers"]["deduplication"] = {"status": "OK"}

        # 2. Taxonomia
        tax_res = validate_taxonomy(payload)
        if tax_res:
            report["valid"] = False
            report["layers"]["taxonomy"] = tax_res
        else:
            report["layers"]["taxonomy"] = {"status": "OK"}

        # 3. Schema (Redis)
        schema_res = validate_schema(payload)
        if schema_res and schema_res.get('status') in ["ERROR","WARNING"]:
            report["valid"] = False
            report["layers"]["schema"] = schema_res
        elif schema_res: # Warning or Skipped
             report["layers"]["schema"] = schema_res
        else:
            report["layers"]["schema"] = {"status": "OK"}

        # 4. Google MP
        mp_res = validate_google_mp(payload)
        if mp_res:
            if mp_res['status'] == "ERROR":
                report["valid"] = False
            report["layers"]["google_mp"] = mp_res
        else:
            report["layers"]["google_mp"] = {"status": "OK"}

        # Gerar versão legível
        readable_report = _format_readable_report(report)
        
        return jsonify({
            "summary": readable_report,
            "details": report
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