import os
import logging
import json
import hashlib
import requests
import re
from flask import Flask, request, jsonify
from google.cloud import firestore
from cachetools import TTLCache
from flasgger import Swagger

# --- CONFIGURAÇÃO ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuração do Swagger
app.config['SWAGGER'] = {
    'title': 'Tagging Validation API',
    'uiversion': 3,
    'description': 'API para validação de eventos de Analytics (Schema, Taxonomia e Google MP)',
    'version': '1.0.0'
}
swagger = Swagger(app)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "tagging-api")
COLLECTION_NAME = 'analytics_event_rules'

# Cliente Firestore (Singleton)
try:
    db = firestore.Client(project=PROJECT_ID)
except Exception as e:
    logger.error(f"Erro no Firestore: {e}")
    db = None

# Cache para Deduplicação (Layer 4)
# Armazena até 1000 hashes. TTL (Time To Live) define a janela de duplicidade (ex: 2 segundos)
dedup_cache = TTLCache(maxsize=1000, ttl=2.0) 

# --- CAMADA 1: DEDUPLICAÇÃO ---
def validate_deduplication(payload_str):
    """
    Gera um hash único do payload. Se existir no cache recente, é duplicado.
    """
    event_hash = hashlib.md5(payload_str.encode('utf-8')).hexdigest()
    
    if event_hash in dedup_cache:
        return {
            "status": "ERROR", 
            "layer": "Deduplication",
            "message": "Evento duplicado detectado em curto intervalo."
        }
    
    # Adiciona ao cache
    dedup_cache[event_hash] = True
    return None

# --- CAMADA 2: TAXONOMIA GLOBAL ---
def validate_taxonomy(event_name, params):
    """
    Verifica padrões de nomenclatura globais (ex: snake_case).
    """
    issues = []
    
    # Regra: Event Names devem ser snake_case
    if not re.match(r'^[a-z0-9_]+$', event_name):
        issues.append(f"Nome do evento '{event_name}' deve ser snake_case (minúsculas e sublinhados).")

    # Regra: Parâmetros não devem começar com 'ga_' ou 'google_' (reservados)
    for param in params.keys():
        if param.startswith(('ga_', 'google_', 'firebase_')):
            issues.append(f"Parâmetro '{param}' usa prefixo reservado proibido.")
        
        # Regra: Parâmetros também devem ser snake_case
        if not re.match(r'^[a-z0-9_]+$', param):
             issues.append(f"Parâmetro '{param}' deve ser snake_case.")

    if issues:
        return {"status": "ERROR", "layer": "Taxonomy", "issues": issues}
    return None

# --- CAMADA 3: MAPA DE COLETA (Firestore) ---
def validate_schema(event_name, params):
    """
    Valida contra as regras específicas do evento no Firestore.
    """
    if not db:
        return {"status": "SKIPPED", "message": "Firestore indisponível"}

    doc_ref = db.collection(COLLECTION_NAME).document(event_name)
    doc = doc_ref.get()

    if not doc.exists:
        return {
            "status": "WARNING", 
            "layer": "Schema",
            "message": f"Evento '{event_name}' não documentado no mapa."
        }

    rules = doc.to_dict().get('parameters', {})
    issues = []

    for param_name, rule in rules.items():
        # Validação de Obrigatório
        if rule.get('required') and param_name not in params:
            issues.append(f"Parâmetro obrigatório ausente: {param_name}")
            continue
        
        # Validação de Regex e Tipo (Simplificado)
        if param_name in params:
            val = str(params[param_name])
            if rule.get('regex') and not re.match(rule['regex'], val):
                 issues.append(f"Valor de '{param_name}' inválido. Regex esperado: {rule['regex']}")

    if issues:
        return {"status": "ERROR", "layer": "Schema", "issues": issues}
    return None

# --- CAMADA 4: GOOGLE MP VALIDATION ---
def validate_google_mp(payload):
    """
    Envia para o endpoint de debug do GA4.
    Requer 'measurement_id' e 'api_secret' no payload ou headers.
    """
    # Extrai credenciais (assumindo que vêm no body para simplificar testes)
    meas_id = payload.get('measurement_id')
    api_secret = payload.get('api_secret')
    
    if not meas_id or not api_secret:
        return {
            "status": "SKIPPED", 
            "layer": "Google Protocol",
            "message": "Measurement ID ou API Secret não fornecidos."
        }

    # Monta o payload no formato que o GA4 espera
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
        google_resp = response.json()
        
        # O endpoint de debug retorna mensagens de validação
        validation_messages = google_resp.get('validationMessages', [])
        
        if validation_messages:
            return {
                "status": "ERROR", 
                "layer": "Google Protocol", 
                "google_feedback": validation_messages
            }
        
    except Exception as e:
        return {"status": "ERROR", "layer": "Google Protocol", "message": str(e)}

    return None

# --- ORQUESTRADOR ---

@app.route('/', methods=['GET'])
def health_check():
    """
    Verificação de Saúde (Health Check)
    ---
    responses:
      200:
        description: API está online
        examples:
          application/json: { "status": "ONLINE", "version": "1.0.0" }
    """
    return jsonify({
        "status": "ONLINE",
        "service": "Tagging Validation API",
        "version": "1.0.0"
    }), 200

@app.route('/refresh-rules', methods=['POST'])
def refresh_rules():
    """
    Cold Start: Carrega regras do BigQuery para o Firestore
    ---
    tags:
      - Admin
    parameters:
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            map_id:
              type: string
              description: ID ou versão específica do mapa de coleta (opcional)
              example: "v1.0_mobile"
    responses:
      200:
        description: Cache atualizado com sucesso
      500:
        description: Erro de conexão com BigQuery
    """
    try:
        payload = request.get_json() or {}
        map_id = payload.get('map_id') # Opcional: carregar versão específica

        logger.info(f"Iniciando refresh do mapa. Map ID: {map_id}")
        
        # 1. Busca e Transforma
        events_data = fetch_map_from_bigquery(map_id)
        
        if not events_data:
            return jsonify({"status": "EMPTY", "message": "Nenhum dado encontrado no BigQuery."}), 404

        # 2. Atualiza Cache
        update_firestore_cache(events_data)
        
        return jsonify({
            "status": "SUCCESS", 
            "message": f"Cache atualizado com {len(events_data)} eventos.",
            "mode": "COLD_START_COMPLETE"
        }), 200

    except Exception as e:
        logger.error(f"Erro no refresh: {e}")
        return jsonify({"status": "ERROR", "message": str(e)}), 500

@app.route('/validate-full', methods=['POST'])
def validate_full():
    """
    Validação Completa (4 Camadas)
    ---
    tags:
      - Validação
    description: Valida deduplicação, taxonomia, schema e envia para o Google.
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
              example: "view_item"
            measurement_id:
              type: string
              example: "G-GX41BSHS2R
            api_secret:
              type: string
              example: "NP8FIX4xTTOJazXz6CPjvw"
            params:
              type: object
              description: Dicionário de parâmetros do evento
              example: 
                item_id: "SKU_123"
                currency: "BRL"
                value: 59.90
    responses:
      200:
        description: Relatório de Validação
        schema:
          type: object
          properties:
            event:
              type: string
            valid:
              type: boolean
            layers:
              type: object
    """
    if not request.is_json:
        return jsonify({"error": "JSON required"}), 400

    raw_payload = request.data.decode('utf-8') # Para o hash exato
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
        return jsonify(report), 200 # Retorna cedo se for duplicado

    report["layers"]["deduplication"] = {"status": "PASS"}

    # 2. Taxonomia Global
    tax_res = validate_taxonomy(event_name, params)
    if tax_res:
        report["valid"] = False
        report["layers"]["taxonomy"] = tax_res
    else:
        report["layers"]["taxonomy"] = {"status": "PASS"}

    # 3. Mapa de Coleta (Schema)
    schema_res = validate_schema(event_name, params)
    if schema_res and schema_res['status'] != "PASS":
        # Nota: Warning no schema não necessariamente invalida, depende da sua regra
        if schema_res['status'] == "ERROR":
            report["valid"] = False
        report["layers"]["schema"] = schema_res
    else:
        report["layers"]["schema"] = {"status": "PASS"}

    # 4. Google Validation (Opcional: Só roda se o resto passar ou força sempre)
    mp_res = validate_google_mp(payload)
    if mp_res:
        if mp_res['status'] == "ERROR":
             report["valid"] = False
        report["layers"]["google_mp"] = mp_res
    else:
        report["layers"]["google_mp"] = {"status": "PASS"}

    return jsonify(report), 200
    # return jsonify({...})
    # return jsonify({"status": "MOCK", "message": "Lógica de validação iria aqui"}), 200

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8080)