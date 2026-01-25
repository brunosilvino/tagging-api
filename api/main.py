import os
import logging
import json
import hashlib
import requests
import re
from flask import Flask, request, jsonify
from google.cloud import firestore
from google.cloud import bigquery
from cachetools import TTLCache
from flasgger import Swagger
import threading
from config import __version__, APP_NAME, APP_DESCRIPTION

# --- CONFIGURAÇÃO ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuração do Swagger
app.config['SWAGGER'] = {
    'title': APP_NAME,
    'uiversion': 3,
    'description': APP_DESCRIPTION,
    'version': __version__
}
swagger = Swagger(app)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
COLLECTION_NAME = 'analytics_event_rules'
BQ_TABLE = "tagging-api-481123.tagging_maps.collection_maps"

# Inicialização de Clientes (Robusta)
db = None
bq_client = None

try:
    db = firestore.Client(project="tagging-api-481123")
    bq_client = bigquery.Client(project=PROJECT_ID)
    logger.info(f"{APP_NAME} v{__version__} - Clientes GCP inicializados.")
except Exception as e:
    logger.error(f"Erro ao inicializar GCP Clients (Verifique key.json): {e}")

# Cache para Deduplicação (Layer 1) - TTL de 2 segundos
DEDUP_TTL = float(os.environ.get('DEDUP_TTL', 2.0))
DEDUP_MAXSIZE = int(os.environ.get('DEDUP_MAXSIZE', 1000))
dedup_cache = TTLCache(maxsize=DEDUP_MAXSIZE, ttl=DEDUP_TTL)
dedup_lock = threading.Lock()

# --- FUNÇÕES AUXILIARES ---
def fetch_map_from_bigquery(map_id=None, map_version=None):
    """
    Vai ao BigQuery, busca as regras e transforma em formato Hierárquico (JSON).
    Se map_id for fornecido, filtra por ele (ex: versão do app ou plataforma).
    """

    if not map_id:
        raise ValueError("map_id é obrigatório para carregar um mapa específico!")

    map_version = f"map_id = '{map_version}'" if isinstance(map_version, int) else 'map_is IS NULL'

    # Query SQL para pegar os dados planos
    # Assumindo colunas: event_name, param_name, param_type, regex_pattern, is_required
    query = f"""
        SELECT *
        FROM `{BQ_TABLE}`
        WHERE {map_id}'
        AND map_version {map_version}
        OR map_version = (SELECT MAX(map_version) FROM `{BQ_TABLE}` WHERE map_id = '{map_id}')
        LIMIT 500
    """

    query_job = bq_client.query(query)
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
        if evt.lower() in ('page_view', 'pageview', 'page_view_event'):
            unique_part = title or page_path or ''
        else:
            unique_part = (section or '') + '_' + (label or '')

        doc_id = _slugify(map_id, map_version, evt, page_path or '', unique_part)

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

def update_firestore_cache(events_data, map_id=None, map_version=None):
    """
    Grava os dados transformados no Firestore em lote (Batch).
    Se map_id e map_version forem fornecidos, remove os documentos antigos
    que correspondem a essa versão antes de inserir os novos.
    """
    batch = db.batch()
    count = 0

    # Se map_id e map_version foram fornecidos, deleta os docs antigos dessa versão
    if map_id and map_version:
        query = db.collection(COLLECTION_NAME).where('metadata.map_id', '==', map_id).where('metadata.map_version', '==', map_version)
        old_docs = list(query.stream())
        for doc in old_docs:
            batch.delete(doc.reference)
            count += 1
            if count >= 400:
                batch.commit()
                batch = db.batch()
                count = 0
    
    # Insere os novos documentos
    for event_name, rules in events_data.items():
        doc_ref = db.collection(COLLECTION_NAME).document(event_name)
        batch.set(doc_ref, rules)
        count += 1
        
        # Firestore batch tem limite de 500 operações. Commit e reinicia se necessário.
        if count >= 400: 
            batch.commit()
            batch = db.batch()
            count = 0
            
    if count > 0:
        batch.commit()
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
    """Layer 3: Valida contra regras do Firestore.

    Recebe o `payload` completo e usa `metadata` (se presente) para buscar o
    documento de forma precisa; caso contrário realiza uma busca baseada em
    `event_name` e possíveis campos presentes dentro de `params`.
    """
    if not db:
        return {"status": "SKIPPED", "message": "🔴 Firestore indisponível (Erro de conexão)"}

    try:
        event_name = payload.get('event_name')
        params = payload.get('params', {}) or {}
        metadata = payload.get('metadata') or {}

        doc_dict = None

        # Se metadata contém map_id/map_version, compõe o doc_id igual ao loader
        if metadata and metadata.get('map_id') and metadata.get('map_version'):
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
            doc_ref = db.collection(COLLECTION_NAME).document(doc_id)
            doc = doc_ref.get()

            if doc.exists:
                doc_dict = doc.to_dict()
            else:
                return {
                    "status": "WARNING",
                    "layer": "Schema",
                    "message": f"⚠️ Evento '{event_name}' não documentado para este contexto."
                }
        else:
            # Busca completa por event_name e filtros extra (params) — semelhante ao loader
            query = db.collection(COLLECTION_NAME).where('event_name', '==', event_name)
            # aplicar filtros em campos comuns armazenados dentro de 'params'
            
            
            if params.get('page_path'):
                query = query.where('params.page_path', '==', params.get('page_path'))
            if params.get('title'):
                query = query.where('params.title', '==', params.get('title'))
            if params.get('section'):
                query = query.where('params.section', '==', params.get('section'))
            if params.get('label'):
                query = query.where('params.label', '==', params.get('label'))

            docs = list(query.stream())
            if not docs:
                return {
                    "status": "WARNING",
                    "layer": "Schema",
                    "message": f"⚠️ Evento '{event_name}' não documentado no mapa de coleta."
                }

            # usa o primeiro documento que corresponde aos filtros
            doc_dict = docs[0].to_dict()

        expected_params = doc_dict.get('params', {})
        issues = []

        for key, value in expected_params.items():
            if key not in params:
                issues.append(f"Parâmetro esperado ausente: {key}")
                continue

            if params.get(key) != value:
                issues.append(f"Valor de '{key}' inválido. Esperado: {value}, recebido: {params.get(key)}")

        if issues:
            return {"status": "ERROR", "layer": "Schema", "issues": issues}
        return None

    except Exception as e:
        logger.error(f"Erro ao ler Firestore: {e}")
        return {"status": "ERROR", "layer": "Schema", "message": str(e)}

def validate_google_mp(payload):
    """Layer 4: Envia para Google Analytics Debug Protocol."""
    meas_id = payload.get('measurement_id')
    api_secret = payload.get('api_secret')
    
    if not meas_id or not api_secret:
        return {"status": "SKIPPED", "layer": "Google Protocol", "message": "Sem credenciais GA4."}

    ga4_payload = {
        "client_id": payload.get("client_id", "test_user"),
        "timestamp_micros": payload.get("timestamp_micros"),
        "events": [{
            "name": payload.get("event_name"),
            "params": payload.get("params", {})
        }]
    }

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
    Cold Start: Carrega regras do BigQuery para o Firestore
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
              example: "v1.0"
          example:
            map_id: "v1.0"
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
        map_version = first_doc.get('metadata', {}).get('map_version')

        # 2. Atualiza Cache (remove antigos do mesmo map_id/map_version e insere novos)
        update_firestore_cache(events_data, map_id=map_id, map_version=map_version)
        
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
    
    if not db:
        return jsonify({"error": "Firestore indisponível"}), 500

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

        # Delete documents in batches without materializing the entire collection
        deleted = 0
        batch = db.batch()
        for i, doc_ref in enumerate(db.collection(COLLECTION_NAME).list_documents(), start=1):
            batch.delete(doc_ref)
            deleted += 1
            if deleted % 400 == 0:
                batch.commit()
                batch = db.batch()

        if deleted % 400 != 0:
            batch.commit()

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
              example: "select_content"
            measurement_id:
              type: string
              example: "G-NF7LZK2M10"
            api_secret:
              type: string
              example: "7IrA3QyPTJaCUe1edtAh3w"
            params:
              type: object
              example: 
                content_type: "article"
                item_id: "12345"
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
            return jsonify(report), 200

        report["layers"]["deduplication"] = {"status": "OK"}

        # 2. Taxonomia
        tax_res = validate_taxonomy(payload)
        if tax_res:
            report["valid"] = False
            report["layers"]["taxonomy"] = tax_res
        else:
            report["layers"]["taxonomy"] = {"status": "OK"}

        # 3. Schema (Firestore)
        schema_res = validate_schema(payload)
        if schema_res and schema_res.get('status') == "ERROR":
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

        return jsonify(report), 200

    except Exception as e:
        logger.error(f"Erro Fatal no validate_full: {e}", exc_info=True)
        return jsonify({"error": "Erro interno no servidor", "details": str(e)}), 500



if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host="0.0.0.0", port=port)