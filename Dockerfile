FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Sem root em produção.
RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000
# `migrate` no start do container, não no build: só nesse momento o
# serviço enxerga o Postgres interno do Render (mesmo padrão do
# forms-meta). `makemigrations` nunca roda aqui — é sempre local, revisado
# e commitado.
CMD ["sh", "-c", "python manage.py migrate && gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000}"]
