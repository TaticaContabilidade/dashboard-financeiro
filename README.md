# dashboard-financeiro

Backend (Django REST) do módulo **Financeiro** da plataforma Tática: dados do
Omie (contas a pagar, contas a receber, fluxo de caixa) servidos para o
React do `forms-meta`.

**Documentação completa na [wiki](https://github.com/TaticaContabilidade/dashboard-financeiro/wiki)**
— arquitetura, requisitos, acesso, banco, sincronização, deploy e registro
de decisões.

## Rodar localmente

Precisa de Docker e Python 3.12.

```bash
cp .env.example .env            # e preencha JWT_PUBLIC_KEY (instruções no arquivo)
docker compose up -d db         # Postgres em localhost:5436
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py runserver 8001
```

Ou tudo em container: `docker compose up --build` (API em `localhost:8001`).

## Testes

```bash
docker compose up -d db
venv/bin/pytest
```

A suíte gera o próprio par de chaves RS256 a cada execução
(`config/settings_test.py`) — não depende do forms-meta nem de chave real.

## Endpoints

| Método | Rota | Acesso |
|--------|------|--------|
| GET | `/api/health/` | Público (health check do Render) |
| GET | `/api/financeiro/meu-acesso/` | Equipe financeira e ADM — diz se o usuário pode alterar |
