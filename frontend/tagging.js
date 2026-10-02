// =====================
// Design Patterns
// 1) Singleton/Guarded Initialization:
//    - O bloco `if (window.gtag == undefined)` garante que o GA4 (gtag)
//      seja inicializado apenas uma vez no escopo global.
// 2) Factory Method (simples):
//    - O método `sendEvent(...)` centraliza a criação do evento com
//      `new this.event(...)`, padronizando o envio.
// 3) Facade (leve):
//    - A classe `Tracker` expõe uma interface simples (`sendEvent`)
//      escondendo detalhes de `gtag`, validação e chamadas `fetch`.
// =====================
const debugMode = true
let counter = 0

/**
 * Objeto global com parâmetros compartilhados em todos os eventos da página
 */
window.trackerObj = {
    map_id: '00003',
    page_path: (location.pathname.includes('/index.html')) ? '/' : location.pathname.replace('.html', ''),
    debug_mode: debugMode,
    page_location: location.href,
    title: document.title,
    //client_id: ga4.clientid,
    event_id: '65f9c171fcee743f1dec1fecb8d98b97'
}
window.trackingObj = window.trackerObj

/**
 * Classe para interagir com a Tagging API.
 * @param {string} endpoint - URL do endpoint da Tagging API.
 * @param {string} api_secret - Segredo da API para autenticação.
 * @param {string} measurement_protocol_api_secret - Chave secreta da API do Measurement Protocol.
 */
class TaggingAPI {
    endpoint = null
    api_secret = null
    measurement_id = null
    measurement_protocol_api_secret = null

    constructor(endpoint, api_secret = '', measurement_id = '', measurement_protocol_api_secret = '') {
        if (!endpoint || typeof endpoint !== 'string') {
            throw new Error('Endpoint inválido para a Tagging API.')
        }
        if (!measurement_id || typeof measurement_id !== 'string' || !measurement_id.startsWith('G-')) {
            console.warn('Measurement ID do GA4 inválido ou não declarado. A API funcionará mas não fará a validação do Measurement Protocol Validation Server.')
        }
        if (!measurement_protocol_api_secret || typeof measurement_protocol_api_secret !== 'string' || !measurement_protocol_api_secret.length > 22) {
            console.warn('API Secrent do Measurement Protocol inválido ou não declarado. A API funcionará mas não fará a validação do Measurement Protocol Validation Server.')
        }

        this.api_secret = api_secret
        this.measurement_id = measurement_id
        this.measurement_protocol_api_secret = measurement_protocol_api_secret
        this.endpoint = endpoint.trim().replace(/\/+$/, '') // Remove barras finais
        return console.log('Tagging API instanciada:', {
            endpoint: this.endpoint,
            measurement_id: this.measurement_id,
        })
    }
    /**
     * Valida um evento na Tagging API.
     * @param {string} event_name Nome do evento a ser validado.
     * @param {object} event_params Parâmetros do evento a serem validados.
     * @param {string} event_id ID do evento a ser validado.
     */
    validate(event_name, event_params, event_id) {
        // console.log('Validando evento na Tagging API:', event_name, event_params)  
        console.log(counter++)
        const url = this.endpoint + '/validate'
        const headers = { 'Content-Type': 'application/json' }
        if (this.api_secret) headers.Authorization = `Bearer ${this.api_secret}`
        if (this.measurement_protocol_api_secret) {
            headers['X-MEASUREMENT-PROTOCOL-API-SECRET'] = this.measurement_protocol_api_secret
        }
        const payload = {
            measurement_id: this.measurement_id,
            map_id: window.trackingObj.map_id,
            event_name,
            params: event_params
        }
        if (event_id!=undefined && event_id !=null)
            Object.assign(payload,{event_id: String(event_id)})

        fetch(url, {
            method: 'POST',
            headers,
            body: JSON.stringify(payload)
        })
        .then(async response => {
            const data = await response.json()
            window.dispatchEvent(new CustomEvent('tagging:validation', {
                detail: {
                    payload,
                    response: data,
                    status_code: response.status,
                    status_text: response.statusText,
                    response_time: response.headers.get('X-Response-Time'),
                    ok: response.ok
                }
            }))
            return data
        })
        .then(data => console.log(data.summary))
        .catch(error => {
            window.dispatchEvent(new CustomEvent('tagging:validation', {
                detail: {
                    payload,
                    response: { error: error.message },
                    status_code: 0,
                    status_text: 'Network error',
                    response_time: null,
                    ok: false
                }
            }))
            console.error('Validation error:', error);
        });
    }

    /**
     * Pré-carrega um mapa na Tagging API.
     * @param {string} map_id ID do mapa a ser carregado.
     * @param {string} map_version Versão do mapa a ser carregado.
     */
    load_map(map_id, map_version) {
        const mapObject = { map_id, map_version }
        const url = this.endpoint + '/load_map'
        fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(mapObject)
        }).then(response => response.json())
    }
}

class Tracker {
    measurement_id = null

    constructor(measurement_id) {
        if (!measurement_id || typeof measurement_id !== 'string' || !measurement_id.startsWith('G-')) {
            throw new Error('Measurement ID inválido para o Google Analytics 4.')
        }

        this.measurement_id = measurement_id
        if (window.gtag == undefined) {
            // Carrega a biblioteca gtag.js
            const script = document.createElement('script')
            script.async = true
            script.src = `https://www.googletagmanager.com/gtag/js?id=${measurement_id}`
            document.head.appendChild(script)

            // Inicializa o gtag
            window.dataLayer = window.dataLayer || []
            function gtag() { dataLayer.push(arguments) }
            window.gtag = gtag
            gtag('js', new Date())
            gtag('config', measurement_id, { 'send_page_view': false })

            console.log('Google Analytics inicializado com o ID:', measurement_id)
        } else {
            console.log('Google Analytics já está inicializado.')
        }
        this.clientid = this.get_ga_clientid()

    }
    get_ga_clientid() {
        const cookie = {};
        document.cookie.split(';').forEach(function (el) {
            var splitCookie = el.split('=');
            var key = splitCookie[0].trim();
            var value = splitCookie[1];
            cookie[key] = value;
        });
        return cookie["_ga"] ? cookie["_ga"].substring(6) : `test.${Date.now()}`;
    }


    /**
     * Classe interna para disparar um evento.
     * @param {string} event_name - Nome do evento.
     * @param {object} event_params - Parâmetros adicionais do evento.
     * @param {function} afterSendCallback - Callback opcional após o envio do evento que recebe event_name e event_params.
     */
    Event = class {
        constructor(event_name, event_params = {}, event_id, afterSendCallback) {
            if (!window.gtag || window.dataLayer == undefined) {
                const advice = 'Inicialize a configuração do Google Analytics primeiro!'
                console.warn(advice)
                window.alert(advice)
                // return
            }
            const gtag = window.gtag
            gtag('event', event_name, event_params)
            console.table({ event_name, ...event_params })
            if (typeof afterSendCallback === 'function') {
                afterSendCallback(event_name, event_params, event_id)
            }
            //this.validate(event_name, event_params)
        }
    }

    removeEmptyKeys(value) {
        if (Array.isArray(value)) {
            return value
                .map(item => this.removeEmptyKeys(item))
                .filter(item => item !== null && item !== undefined && item !== '')
        }

        if (value && typeof value === 'object') {
            return Object.fromEntries(
                Object.entries(value)
                    .filter(([, item]) => item !== null && item !== undefined && !(typeof item === 'string' && item.trim() === ''))
                    .map(([key, item]) => [key, this.removeEmptyKeys(item)])
            )
        }

        return value
    }

    sendEvent(event_name, event_params = {}, event_id=null, afterSendCallback) {
        const cleanedParams = this.removeEmptyKeys(event_params)
        new this.Event(event_name, cleanedParams, event_id, afterSendCallback)
    }
}

const ga4 = new Tracker('G-GX41BSHS2R')  // Substitua pelo seu Measurement ID do GA4
const taggingAPI = new TaggingAPI(
    window.TAGGING_API_URL || 'http://localhost:8080',
    window.TAGGING_API_KEY || '',
    window.TAGGING_MEASUREMENT_ID || '',
    window.TAGGING_MEASUREMENT_PROTOCOL_SECRET || ''
)

//PAGEVIEW
document.addEventListener('DOMContentLoaded', (event) => {
    ga4.sendEvent('page_view', {
        debug_mode: debugMode,
        page_location: location.href,
        page_path: window.trackingObj.page_path,
        title: document.title,
        client_id: ga4.clientid,
    }, '65f9c171fcee743f1dec1fecb8d98b97', taggingAPI.validate.bind(taggingAPI))
})

//FIELDS
document.querySelectorAll('input[type=text], input[type=email], input[type=password], input[type=tel], textarea').forEach(element => {
    element.addEventListener('change', (ev) => {
        const el = ev.currentTarget
        const type = element.type.toLowerCase() || null
        const event_id = element.getAttribute('event-id') || null
        let tag_name = el.tagName.toLowerCase()
        tag_name = tag_name =='textarea' ? tag_name : [tag_name, type].join('-')
        const section = el.closest('section, nav, header, footer', 'dialog', 'form', 'aside')

        ga4.sendEvent('field_fill', {
            debug_mode: debugMode,
            page_location: location.href,
            page_path: window.trackingObj.page_path,
            component: tag_name,
            section: section ? (section.id || section.tagName.toLowerCase()) : null,
            label: el.closest('label') ? (el.closest('label').innerText || el.closest('label').textContent || '').trim().substring(0, 100).toLowerCase() : null,
            client_id: ga4.clientid
        }, event_id,taggingAPI.validate.bind(taggingAPI))
    })
})

document.querySelectorAll('select, input[type=radio], input[type=combobox]').forEach(element => {
    element.addEventListener('change', (ev) => {
        const el = ev.currentTarget
        const event_id = el.getAttribute('event-id') || null
        let tag_name = el.tagName.toLowerCase()
        const section = el.closest('section, nav, header, footer', 'dialog', 'form', 'aside')
        tag_name == 'select' ? tag_name : [tag_name, type].join('-')

        ga4.sendEvent('field_select', {
            event_id: element.getAttribute('event-id') || null,
            debug_mode: debugMode,
            page_location: location.href,
            page_path: window.trackingObj.page_path,
            component: tag_name,
            section: section ? (section.id || section.tagName.toLowerCase()) : null,
            label: `assunto:${el.value}`,
            client_id: ga4.clientid
        }, event_id, taggingAPI.validate.bind(taggingAPI))
    })
})

document.querySelectorAll('form').forEach(form => {
    form.addEventListener('submit', (ev) => {
        ev.preventDefault() 
        const el= ev.target
        const event_id = el.getAttribute('event-id') || null
        const section = el.closest('section, nav, header, footer', 'dialog', 'form', 'aside')

        ga4.sendEvent('form_submit', {
            event_id: form.getAttribute('event-id') || null,
            debug_mode: debugMode,
            page_location: location.href,
            page_path: window.trackingObj.page_path,
            component: 'form',
            section: section ? (section.id || section.tagName.toLowerCase()) : null,
            label: form.id || form.name || null,
            client_id: ga4.clientid
        }, event_id, taggingAPI.validate.bind(taggingAPI))
        form.querySelector('[data-form-status]').textContent = 'Formulário enviado para validação.'
        return false //Para teste
    })
})

//CLICKS
document.querySelectorAll('a, button:not([type=submit])').forEach(element => {
    element.addEventListener('click', (ev) => {
        ev.preventDefault() //Para teste
        console.log('clicou')
        const el = ev.currentTarget
        const tag_name = el.tagName.toLowerCase()
        const text = (el.innerText || el.textContent || '').replaceAll(/\s/g, '-').toLowerCase().trim().substring(0, 100)
        const href = (tag_name === 'a') ? el.getAttribute('href') : null
        const outbound = (href !== null && !href.includes(location.hostname) && href.startsWith('http')) ? true : false
        const section = el.closest('section, nav, header, footer', 'dialog', 'form', 'aside')
        const component = tag_name === 'a' ? 'link' : tag_name
        const event_id = el.getAttribute('event-id') || null

        ga4.sendEvent('click', {
            debug_mode: debugMode,
            page_location: location.href,
            page_path: window.trackingObj.page_path,
            component: component,
            outbound: component == 'link' ? outbound : null, 
            section: section ? (section.id || section.tagName.toLowerCase()) : null,
            label: text,
            client_id: ga4.clientid
        }, event_id, taggingAPI.validate.bind(taggingAPI))
    })
})

window.addEventListener('tagging:validation', (event) => {
    const detail = event.detail
    const responseSucceeded = detail.status_code >= 200 && detail.status_code < 300
    document.querySelector('#validation-status').innerHTML = detail.status_code
        ? `HTTP ${detail.status_code}• ${detail.status_text} <br> Validação ${responseSucceeded ? 'aprovada' : 'reprovada'}`
        : 'Falha ao consultar a API'
    document.querySelector('#validation-status').classList.toggle('success', responseSucceeded)
    document.querySelector('#validation-status').classList.toggle('error', !responseSucceeded)
    document.querySelector('#validation-payload').textContent = JSON.stringify(detail.payload, null, 2)
    document.querySelector('#validation-response').textContent = JSON.stringify(detail.response, null, 2)
    const summary = detail.response.summary || detail.response.error || 'Sem resumo disponível.'
    const responseTime = detail.response_time ? `\n\nTempo de resposta: ${detail.response_time}` : ''
    document.querySelector('#validation-response-formatted').textContent = summary + responseTime

})

function renderTrackerPayload() {
    const payloadBody = document.querySelector('#tracker-payload-body')
    if (!payloadBody) return

    Object.entries(window.trackerObj).forEach(([key, value]) => {
        const row = document.createElement('tr')
        const field = document.createElement('th')
        const content = document.createElement('td')

        field.scope = 'row'
        field.textContent = key
        content.textContent = value ?? ''
        row.append(field, content)
        payloadBody.appendChild(row)
    })
}

document.addEventListener('DOMContentLoaded', renderTrackerPayload)