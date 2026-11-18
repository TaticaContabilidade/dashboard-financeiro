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
- **Diretor da Tática acessa o financeiro (D-30).** O Diretor passa a ser
  o papel dos donos de cada empresa, inclusive da Tática. Em
  `AcessoFinanceiro`, o Diretor da empresa interna vê e altera,
  independentemente do setor. O Diretor de empresa cliente continua sem
  acesso, porque os dados são da conta Omie da Tática (D-10). Testes: perfil
  `diretor_tatica` (sem marcador de setor financeiro) passa a receber 200
  com `pode_alterar: true`; perfil novo `diretor_cliente` recebe 403 (53
  testes).

## Alterações branch feat/estrutura-multiempresa

### 2026-11-09

Estrutura para o financeiro **multiempresa** (D-32): cada empresa cliente
acessa os dados do **próprio** Omie. Fica pronta a fundação; a
sincronização e as telas vêm depois. Branch empilhada sobre
`feat/estrutura-backend` (PR #1).

- **Regra de acesso** (`apps/acesso/permissoes.py`): deixa de depender de
  `empresa_interna`. Na própria empresa: ADM (só o desenvolvedor, D-31) e
  Diretor (donos) leem e alteram em qualquer setor; Gerente do setor
  financeiro lê e altera; Colaborador do setor financeiro só lê. Token sem
  `empresa_id` inteiro e positivo é recusado (não dá para escopar os
  dados) — inclusive `true`, que em Python é `int`.
- **Trava de empresa** (`apps/acesso/escopo.py`): `empresa_da_requisicao`
  decide de qual empresa são os dados — a do token; só o ADM escolhe outra
  com `?empresa_id=`. Não-ADM pedindo outra empresa recebe 403 (nunca é
  ignorado em silêncio). `EscopoEmpresaMixin` aplica isso em
  `get_queryset()` e grava a empresa no `perform_create()`, ignorando o
  body.
- **Base dos models** (`apps/core/models.py`): `ModeloDaEmpresa` (abstrato,
  `empresa_id` indexado, sem FK porque as empresas moram no banco do
  forms-meta) e `EscopoEmpresaQuerySet.da_empresa(id)`, que falha alto com
  `None` em vez de devolver vazio.
- **Conta Omie por empresa** (`apps/omie`): model `ContaOmie` (1 por
  empresa, tabela `omie_contas`) com `app_key`/`app_secret` cifrados com
  Fernet (`apps/omie/cifra.py`). A chave vem de `OMIE_CREDENCIAIS_CHAVE`
  (nunca do banco), aceita rotação com várias chaves e só é exigida quando
  uma credencial é usada. `repr`/`str` não mostram credenciais.
  - Comando `configurar_conta_omie` (credenciais pedidas sem eco, nunca por
    argumento) e `recifrar_contas_omie` (rotação de chave).
  - Migration `omie/0001_initial`.
- `meu-acesso` passa a devolver também o `empresa_id`.
- `OMIE_APP_KEY`/`OMIE_APP_SECRET` saem do `.env.example`: as credenciais
  agora são por empresa, no banco. Entra `OMIE_CREDENCIAIS_CHAVE`.
- Testes: 118 passando — regra de acesso multiempresa, trava de empresa
  (direto e numa view de exemplo com o mixin), criptografia (ida e volta,
  chave errada, rotação), credenciais cifradas no banco (conferido na
  coluna bruta), uma conta por empresa e os dois comandos. Prova de
  sabotagem: liberar outra empresa para qualquer papel quebra 3 testes.

## Alterações branch feat/cliente-omie

### 2026-11-10

Cliente HTTP da API do Omie e comando de teste com uma conta real (wiki:
Integração Omie).

- **`apps/omie/cliente.py` — `ClienteOmie`:** `chamar()` faz o POST JSON
  (`call`, `app_key`, `app_secret`, `param`) e `listar()` percorre as
  páginas (campos de página configuráveis, porque o `ListarMovimentos` usa
  `nPagina`/`nRegPorPagina`/`nTotPaginas`). Atalhos para os métodos
  mapeados: movimentos, contas a pagar/receber, contas correntes,
  categorias, clientes e saldo (`ListarExtrato` só com saldos). "Não
  existem registros" vira lista vazia. Log só com serviço, método, página,
  status e duração — nunca credencial nem conteúdo. `repr` não mostra
  credencial.
- **Novas tentativas só para falha passageira de rede/5xx** (2, com espera
  de 2 s e 5 s). Limite, consulta redundante, bloqueio (425), credencial e
  erro permanente sobem como exceção na primeira vez: insistir aproxima o
  bloqueio de 30 min que o Omie aplica depois de 10 erros.
- **`apps/omie/erros.py`:** hierarquia de erros pelo que a sincronização
  deve fazer (`ErroTransitorio`, `ErroLimite`, `ErroRedundante`,
  `ErroBloqueio`, `ErroCredencial`, `ErroPermanente`) e `classificar()`.
  Fault desconhecido cai em permanente (não insistir). Formato real de
  credencial inválida confirmado com uma chamada ao Omie usando uma chave
  inventada: HTTP 403, "A chave de acesso não está preenchida ou não é
  válida.".
- **`apps/omie/limitador.py`:** trava de taxa por janela deslizante de 60 s,
  com 800/min por IP (Omie: 960, somando todas as empresas) e 200/min por
  app_key + método (Omie: 240). A documentação não diz se a janela do Omie
  é fixa ou deslizante; a deslizante é a mais restritiva, então serve para
  as duas. Um limitador por processo.
- **Comando `testar_conta_omie --empresa-id N`:** ~10 leituras que
  respondem as pendências do mapeamento (tamanho máximo de página, regra
  dos 30 dias no filtro de alteração, se `ListarMovimentos` traz natureza,
  status, pagamento e conta corrente, campos de saldo). Imprime só
  contagens, nomes de campos, tipos e nomes de status. O teste do comando
  injeta valores sentinela (razão social, CNPJ, valores) e confere que
  nenhum aparece na saída.
- Dependência nova: `requests==2.33.1`.
- README e `.env.example` passam a usar `.venv/` nos exemplos.
- Testes: 152 passando (classificação de erros, janela deslizante, inclusive
  "20 empresas somam no mesmo teto de IP", formato da chamada, paginação,
  novas tentativas, nada de credencial no log, comando sem vazar valores).

### 2026-11-12

- **Teste real com a conta da Tática** (D-34 na wiki): página máxima de 100,
  filtro `dDtAltDe` sem a regra dos 30 dias, `ListarMovimentos` serve como
  fonte principal. A paginação do cliente leu as 74 páginas sem truncar.
- **Etapa 8 no `testar_conta_omie`:** antes de fixar a chave única da tabela
  de movimentos, conta (sem chamar o Omie de novo, reaproveitando a etapa 4)
  quantos `nCodTitulo` se repetem, quantas linhas colidem em cada chave
  candidata, os códigos de `cGrupo`/`cOrigem`/`cTipo` e se os valores vêm
  como `int` ou `float`. Nunca imprime ids nem valores; código com mais de
  25 caracteres vira `<texto longo>`, para um campo livre não vazar. Testes:
  154 (conferido que o teste quebra se o filtro de texto for afrouxado).
