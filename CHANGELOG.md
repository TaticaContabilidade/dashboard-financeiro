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

## Alterações branch feat/teste-omie-etapas-9-10

### 2026-11-18

- **Etapas 9 e 10 no `testar_conta_omie`** (reaproveitam os movimentos da
  etapa 4, sem nova chamada ao Omie; só nomes de campos e contagens):
  - **9:** para cada `cGrupo`, os campos presentes em todas as linhas (a
    etapa 3 só mostrava o 1º movimento), qual campo `nCod*` identifica a
    linha sem colidir e quantos títulos das baixas/previsões existem em
    `CONTA_A_*`.
  - **10:** colisões da chave da conta corrente, hipótese de baixa em lote
    (soma dos `nValPago` = `nValorMovCC`) e se a previsão de contrato
    convive com o título já faturado.
- Resultado com a conta da Tática fixou as chaves e as regras de cálculo
  (D-36 na wiki). Testes: 156 (incluindo que ids, contratos e parcelas
  nunca aparecem na saída).

## Alterações branch feat/tabelas-titulos-lancamentos

### 2026-11-18

- **App `apps.financeiro` com as duas tabelas aprovadas (D-37)**, ambas
  herdando `ModeloDaEmpresa`:
  - `financeiro_titulos` (`Titulo`): contas a pagar, a receber e previsões
    de contrato. Chave única `empresa_id + tipo + codigo_titulo`
    (`nCodTitulo`). Base de contas a pagar/receber e do fluxo previsto.
  - `financeiro_lancamentos_conta` (`LancamentoConta`): o que passou na
    conta corrente (baixas, lançamentos diretos, transferências). Chave
    única `empresa_id + natureza + codigo_movimento + codigo_baixa`
    (`nCodMovCC`/`nCodBaixa`, 0 quando ausente), com check exigindo pelo
    menos um dos dois. Base do fluxo realizado.
  - Sem FK entre as tabelas: um lançamento pode chegar antes do título.
  - Valores em `DecimalField(15, 2)`, nulos quando o Omie não informa.
- **`Titulo.objects.previsoes_validas()`:** ignora a previsão de contrato
  quando a mesma empresa já tem título a receber do mesmo contrato no mesmo
  mês (previsão velha até a reconciliação, D-36).
- **`apps/omie/conversao.py`:** converte uma linha do `ListarMovimentos`
  para a tabela certa pelo `cGrupo` (chave + campos, sem banco). Valores
  por `Decimal(str(x))` com 2 casas; datas `dd/mm/aaaa`; linha sem
  identificador, `cGrupo` desconhecido ou formato inválido levanta
  `LinhaIgnorada` com motivo seguro para log (sem valor do Omie).
- Testes: 192 (36 novos). Conferido que quebram se a trava de empresa da
  regra da previsão for removida e se o lançamento usar `nValorMovCC`.

## Alterações branch feat/cadastros-saldos-execucoes

### 2026-11-18

- **Cadastros** (`apps.financeiro`, todos com unicidade `empresa_id + codigo`):
  `ContaCorrente` (`financeiro_contas_correntes`, com `fora_do_fluxo` vindo
  do `nao_fluxo`), `Categoria` (`financeiro_categorias`) e
  `ClienteFornecedor` (`financeiro_clientes_fornecedores`). Por minimização
  (D-38) não guardam CPF/CNPJ, endereço, contato, agência nem número de
  conta: só o necessário para mostrar nomes no dashboard.
- **Saldos:** `SaldoConta` (`financeiro_saldos`), um registro por conta por
  dia (`empresa_id + codigo_conta_corrente + data`), com os campos do
  `ListarExtrato` confirmados no teste real.
- **Execuções da sincronização:** `ExecucaoSincronizacao` (`omie_execucoes`)
  com tipo, situação, período pedido ao Omie (`alterados_desde/ate`),
  contagens e erro. `ultima_bem_sucedida()` dá o ponto de corte e o
  "atualizado há X min"; execução com erro nunca avança o corte (RNF-11).
- **Conversões** em `apps/omie/conversao.py` para conta corrente,
  categoria, cliente/fornecedor e saldo.
- **Etapa 11 do `testar_conta_omie`:** os campos de categoria e de cliente
  vieram só da documentação; a etapa converte os cadastros reais e mostra
  quantos converteram e quais campos ficaram preenchidos (só contagens e
  nomes de campos; 1 chamada nova, amostra de 50 clientes).
- Testes: 213 (21 novos). Conferido que quebram se a conversão do cliente
  guardar o CNPJ e se o ponto de corte considerar execução com erro.
- **Etapa 11 rodada com a conta da Tática:** contas correntes 13/13,
  categorias 280/280 e amostra de 50 clientes 50/50 convertidos, sem
  ignorados; nomes de campos confirmados. Porém `nao_fluxo = S` em 11 das
  13 contas: a etapa 11 passa a contar quantos lançamentos de conta
  corrente caem em contas marcadas "fora do fluxo", antes de usarmos a
  marcação como filtro. Testes: 214.

## Alterações branch feat/sincronizacao

### 2026-11-19

- **Sincronização por empresa** (`apps/omie/sincronizacao.py`,
  `Sincronizador`), backlog 3.1 a 3.5:
  - **Incremental:** movimentos alterados do ponto de corte até hoje (o dia
    do corte é relido de propósito) e saldo de hoje das contas ativas.
    Sem execução anterior, faz a carga completa.
  - **Reconciliação:** relê contas correntes, categorias, clientes e todos
    os movimentos (`dDtAltDe` = 01/01/2000) e só depois, com tudo lido sem
    erro, apaga o que sumiu do Omie. Nunca apaga mais de 20% de uma tabela
    de uma vez (acima de 50 linhas): registra como `exclusoes_bloqueadas`.
  - **Lock por empresa** (`pg_try_advisory_lock`): se a rodada anterior
    ainda está na empresa, esta pula.
  - **Erros:** credencial recusada suspende a empresa até
    `configurar_conta_omie` gravar credenciais novas; bloqueio 425 e erro
    permanente pausam 30 min; erro passageiro só registra. O ponto de
    corte nunca avança com erro. Erro inesperado (bug) é registrado só com
    a classe, sobe e o comando sai com código 1.
  - Gravação em lote com upsert (`bulk_create(update_conflicts=True)`),
    chave repetida no mesmo lote fica com a última.
- `ContaOmie`: campos `suspensa_em`, `motivo_suspensao`, `pausada_ate` e
  `pode_sincronizar()`; credencial nova limpa suspensão e pausa.
- **Comando `sincronizar_omie`** (`--reconciliacao`, `--empresa-id`): uma
  empresa por vez, a falha de uma não para as outras, saída só com
  contagens.
- **docker-compose:** Postgres e API publicados só em `127.0.0.1`, porque o
  banco local passa a guardar dados financeiros reais.
- Testes: 235 (21 novos). Sabotagens conferidas: exclusão sem trava de
  empresa, erro avançando o corte, exclusão em massa sem proteção e lock
  ignorado quebram os testes.


## Alterações branch feat/api-dashboard

### 2026-11-24

- **API do dashboard** (etapa 4 do backlog; wiki: API do Dashboard),
  escrita a partir da base feita com o agente Antigravity e revisada aqui:
  - `GET contas-a-pagar/` e `contas-a-receber/`: lista paginada com
    situação derivada (pago, vencido, a vencer), nome do cliente/fornecedor
    e da categoria, e `resumo` por situação somado sobre todos os títulos
    filtrados;
  - `GET fluxo-de-caixa/`: realizado (conta corrente, por pagamento) e
    previsto (títulos em aberto + previsões válidas, por vencimento), por
    dia, semana ou mês;
  - `GET ultima-atualizacao/`: "atualizado há X min" (RF-04).
- **Regras de negócio provisórias (D-40)**, para o setor financeiro ajustar
  depois. A lista do que revisar está na wiki.
- **Correções em relação à base:**
  - o resumo agrupava título por título (a ordenação entrava no
    `GROUP BY`) e mostrava só o último de cada situação;
  - títulos cancelados (o Omie os manda como liquidados) apareciam como
    pagos: agora ficam fora de tudo (`Titulo.objects.sem_cancelados()`);
  - baixa parcial contava duas vezes no fluxo (pago no realizado + valor
    inteiro no previsto): o previsto e o resumo usam `valor_aberto`, e o
    resumo dos pagos usa `valor_pago`;
  - data inválida dava erro 500 nas contas e era ignorada em silêncio no
    fluxo: agora é 400 com o nome do campo, assim como período invertido,
    situação e granularidade fora da lista;
  - dinheiro saía como float no resumo e no fluxo: agora sempre texto com
    2 casas, como nos campos das listas;
  - previsões somadas pela natureza, não sempre como entrada.
- **docker-compose:** a interface da rede no host tem nome fixo
  (`br-dashfin`), para a regra do firewall do debugger do PyCharm valer
  depois de um `docker compose down`.
- Testes: 276 (41 da API). Testes de vazamento e de 403 em todos os
  endpoints; respostas lidas como JSON de verdade. Sabotagens conferidas:
  sem filtro de cancelados, valor do título no lugar do aberto, resumo sem
  `.order_by()`, dinheiro como float, nome do fornecedor de outra empresa,
  fluxo sem trava de empresa e data inválida ignorada quebram os testes.

## Alterações branch feat/teste-omie-etapa-12

### 2026-11-24

- **Etapa 12 do `testar_conta_omie`: rateio de categorias** (backlog 4.8,
  D-41). Lê todas as contas a pagar e a receber e mede quantos títulos têm
  rateio entre categorias (e quanto do valor), se o rateio atravessa
  grupos (`2.03` × `2.04`), por ano de vencimento, se a soma das partes
  fecha com o título e qual categoria o `ListarMovimentos` usa num título
  rateado. Base para decidir se o gráfico de gastos por categoria pode
  usar a categoria do movimento. Só contagens e percentuais.
- Testes: 1 novo (sem valores nem textos na saída). Sabotagem conferida:
  agrupar pelo código inteiro em vez do grupo quebra o teste.
