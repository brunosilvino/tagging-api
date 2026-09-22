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
const mapID = '00001'
let counter = 0

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

    constructor(endpoint, api_secret, measurement_id, measurement_protocol_api_secret) {
        if (!endpoint || typeof endpoint !== 'string' ) {
            throw new Error('Endpoint inválido para a Tagging API.')
        }
        if (!api_secret || typeof api_secret !== 'string') {
            throw new Error('API secret inválido para a Tagging API.')
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
        this.endpoint = endpoint.trim().replace(/\/+$/,'') // Remove barras finais
        return console.log('Tagging API instanciada:', {
            endpoint: this.endpoint, 
            measurement_id: this.measurement_id,
        })
    }
    /**
     * Valida um evento na Tagging API.
     * @param {string} event_name Nome do evento a ser validado.
     * @param {object} event_params Parâmetros do evento a serem validados.  
     */
    validate(event_name, event_params){
        // console.log('Validando evento na Tagging API:', event_name, event_params)  
        console.log(counter++)      
        const url = this.endpoint + '/validate'
        fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${this.api_secret}`,
            },
            body: JSON.stringify({
                measurement_id: this.measurement_id,
                measurement_protocol_api_secret: this.measurement_protocol_api_secret,
                map_id: mapID,
                event_name, params: event_params
            })
        })
        .then(response => response.json())
        .then(data => console.log(data.summary))
        .catch(error => {
            console.error('Validation error:', error);
        });
    }

    /**
     * Pré-carrega um mapa na Tagging API.
     * @param {string} map_id ID do mapa a ser carregado.
     * @param {string} map_version Versão do mapa a ser carregado.
     */
    load_map(map_id, map_version){
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
        document.cookie.split(';').forEach(function(el) {
            var splitCookie = el.split('=');
            var key = splitCookie[0].trim();
            var value = splitCookie[1];
            cookie[key] = value;
        });
        return cookie["_ga"].substring(6);
    }


    /**
     * Classe interna para disparar um evento.
     * @param {string} event_name - Nome do evento.
     * @param {object} event_params - Parâmetros adicionais do evento.
     * @param {function} afterSendCallback - Callback opcional após o envio do evento que recebe event_name e event_params.
     */
    Event = class {
        constructor(event_name, event_params = {}, afterSendCallback) {
            if (!window.gtag || window.dataLayer == undefined) {
                const advice = 'Inicialize a configuração do Google Analytics primeiro!'
                console.warn(advice)
                window.alert(advice)
                // return
            }
            const gtag = window.gtag
            gtag('event', event_name, event_params)
            console.table({ event_name, ...event_params })
            afterSendCallback(event_name, event_params)
            //this.validate(event_name, event_params)
        }
    }

    sendEvent(event_name, event_params = {},afterSendCallback) {
        new this.Event(event_name, event_params,afterSendCallback)
    }
}

const ga4 = new Tracker('G-GX41BSHS2R')  // Substitua pelo seu Measurement ID do GA4
const taggingAPI = new TaggingAPI(
    // 'https://tagging-api-azvnjols4q-rj.a.run.app',
    (env === 'dev') ? 'http://localhost:8080' : 'https://tagging-api-azvnjols4q-rj.a.run.app',
    '7IrA3QyPTJaCUe1edtAh3w',// Substitua pelo seu API Secret da Tagging API
    'G-GX41BSHS2R',          // Substitua pelo seu Measurement ID do GA4
    'NP8FIX4xTTOJazXz6CPjvw' // Substitua pelo seu Measurement Protocol Secret do GA4
)


//PAGEVIEW
document.addEventListener('DOMContentLoaded', (event) => {
    ga4.sendEvent('page_view', {
        debug_mode: debugMode,
        page_location: location.href,
        page_path: (location.pathname.includes('/index.html')) ? '/' : location.pathname.replace('.html',''),
        title: document.title,
        client_id: ga4.clientid,
    }, taggingAPI.validate.bind(taggingAPI))
})

//FIELDS
document.querySelectorAll('input[type=text], input[type=email], input[type=password], input[type=tel], textarea').forEach(element => {
    element.addEventListener('change', (ev) => {
        const el = ev.currentTarget
        const tag_name = el.tagName.toLowerCase()
        const text = (el.value || '').trim().substring(0, 100)
        const id = el.id || null
        const classes = el.className || null
        const name = el.getAttribute('name') || null
        

        ga4.sendEvent('field_fill', {
            debug_mode: debugMode,
            page_location: location.href,
            page_path: location.pathname,
            component: tag_name,
            section: el.closest('form') ? 'form' : null,
            label: el.closest('label') ? (el.closest('label').innerText || el.closest('label').textContent || '').trim().substring(0, 100) : null,
            client_id: ga4.clientid
        }, taggingAPI.validate.bind(taggingAPI))
    })
})

//CLICKS
document.querySelectorAll('a, button').forEach(element => {
    element.addEventListener('click', (ev) => {
        console.log('clicou')
        const el = ev.currentTarget
        const tag_name = el.tagName.toLowerCase()
        const text = (el.innerText || el.textContent || '').trim().substring(0, 100)
        const id = el.id || null
        const classes = el.className || null
        const href = (tag_name === 'a') ? el.getAttribute('href') : null
        const outbound = (href !== null && !href.includes(location.hostname) && href.startsWith('http')) ? true : false
        const section = el.closest('section, nav, header, footer', 'dialog')
        const component = tag_name === 'a' ? 'link' : (tag_name === 'button' ? 'botao' : null)

        ga4.sendEvent('click', {
            debug_mode: debugMode,
            page_location: location.href,
            page_path: location.pathname,
            component: tag_name,
            outbound: outbound,
            section: section.id || section.tagName.toLowerCase() || null,
            label: [component, text].join(':').trim().replaceAll(/\s/g, '-').toLowerCase(),
            client_id: ga4.clientid
        }, taggingAPI.validate.bind(taggingAPI))
    })
})