# Usa uma imagem oficial leve do Python
FROM python:3.10-slim

# Define variáveis de ambiente para produção
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Define o diretório de trabalho
WORKDIR /app

# Copia apenas requirements.txt para otimizar cache
COPY requirements.txt .

# Instala dependências
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir flask gunicorn

# Copia o restante do código
COPY . .

# Expõe a porta (Cloud Run padrão)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/', timeout=5)"

# Usa gunicorn em produção (mais robusto que o servidor Flask)
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 4 --worker-class sync --timeout 120 main:app
CMD exec gunicorn --bind :8080 --workers 1 --threads 8 --timeout 0 main:app