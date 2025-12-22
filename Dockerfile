# Usa uma imagem oficial leve do Python
# python:3.10-slim é menor e mais segura que a versão completa
FROM python:3.10-slim

# Define variáveis de ambiente
# PYTHONUNBUFFERED=1 garante que os logs apareçam imediatamente no console (sem buffer)
ENV PYTHONUNBUFFERED=1

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Copia apenas o arquivo de dependências primeiro
# Isso otimiza o cache do Docker: se você mudar o código mas não as libs,
# ele não precisa instalar tudo de novo.
COPY requirements.txt .

# Instala as dependências
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do código para dentro do container
COPY . .

# Expõe a porta 8080 (padrão do Cloud Run)
EXPOSE 8080

# Comando para iniciar a aplicação usando Gunicorn
# --workers 1 --threads 8: Configuração recomendada para Cloud Run (concorrência)
# --timeout 0: Remove timeout do gunicorn para deixar o Cloud Run gerenciar
CMD exec gunicorn --bind :8080 --workers 1 --threads 8 --timeout 0 main:app