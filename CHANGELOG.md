# CHANGELOG

Diário técnico do repositório, organizado por branch (ver wiki, Fluxo de
Trabalho). Mais recente por último dentro de cada branch.

## Alterações branch feat/estrutura-backend

### 2026-11-06

- **Estrutura do backend Django + DRF** na raiz do repositório (`config/`,
  `apps/`), mesma stack e versões do `forms-meta/backend` (Django 6.1.1,
  DRF 3.18.1, simplejwt 5.5.1). Sem `django.contrib.admin`/`sessions`:
  este serviço não tem usuários nem login (D-27).
  - `requirements.in` lista as dependências diretas; `requirements.txt`
    é o `pip freeze` completo, fixado.
- **Validação do JWT RS256 emitido pelo forms-meta** (D-20, D-26):
  `JWTStatelessUserAuthentication` do simplejwt — o usuário vem só do
  token (`TokenUser`), sem consulta a banco. `SIGNING_KEY = None`: este
  serviço só verifica, nunca emite. `JWT_PUBLIC_KEY` é obrigatória: sem
  ela o processo falha no boot (`ImproperlyConfigured`) em vez de subir
  recusando tudo em silêncio. O decode só aceita RS256, então tokens HS256
  antigos do forms-meta são recusados.
- **Permissão única do Financeiro** em `apps/acesso/permissoes.py`
  (`AcessoFinanceiro`, permissão padrão do DRF — deny-by-default):
  ADM lê e altera; Gerente do setor financeiro da empresa interna lê e
  altera; Colaborador desse setor só lê; os demais (Diretor, outros
  setores, empresas clientes) recebem 403. Detalhes de segurança:
  - claims booleanas (`empresa_interna`, `setor_financeiro`) só valem se
    forem o booleano `true` do JSON — string `"false"` seria truthy;
  - `user_id: null` é barrado na permissão (o simplejwt só checa se a
    claim existe).
- **Endpoints:** `GET /api/health/` (público, `SELECT 1` no banco, 503 se
  cair) e `GET /api/financeiro/meu-acesso/` (diz ao React se o usuário pode
  alterar — conveniência de interface; a checagem real continua no
  servidor em cada endpoint).
- **Testes** (`pytest-django`, 48): `config/settings_test.py` gera um par
  de chaves RSA a cada execução e os testes assinam os próprios tokens,
  imitando o forms-meta — nenhuma chave versionada, sem depender do login
  novo. Cobrem 401 (sem token, outra chave, HS256, expirado, refresh no
  lugar de access, sem `user_id`, header mal formado), 200/403 por papel,
  claims em formato inválido, regra de escrita por método HTTP e health
  check (inclusive banco fora do ar). Conferido que a suíte quebra (19
  falhas) quando a permissão é sabotada para liberar tudo.
- **Docker:** `Dockerfile` (python 3.12-slim, usuário sem root, `migrate`
  no start e gunicorn na `$PORT`) e `docker-compose.yml` com Postgres 16 em
  `localhost:5436` e API em `localhost:8001` — portas escolhidas para não
  colidir com o forms-meta (5433/5434/8000). `.dockerignore` mantém
  `__init__.py` apesar da regra `_*`.
- Testado manualmente no container: health 200, `meu-acesso` com tokens
  RS256 reais (ADM/Gerente/Colaborador 200, Diretor/cliente 403, sem token
  401), imagem de produção com gunicorn rodando como usuário `app`, e boot
  recusado sem `JWT_PUBLIC_KEY`.
- `.env.example` documenta as variáveis e como gerar um par de chaves de
  desenvolvimento (arquivos com prefixo `_`, fora do git).
