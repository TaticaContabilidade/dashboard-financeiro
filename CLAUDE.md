# CLAUDE.md

Orientações para trabalhar neste repositório (`dashboard-financeiro`).

## Documentação

**Toda a documentação do projeto fica na wiki do GitHub** —
https://github.com/TaticaContabilidade/dashboard-financeiro/wiki — um
repositório git separado, clonado localmente em
`../dashboard-financeiro.wiki` (`git@github.com:TaticaContabilidade/dashboard-financeiro.wiki.git`).
Comece pela `Home.md` (mapa da wiki) e pelo `_Sidebar.md` (menu). Páginas:
requisitos funcionais/não funcionais, arquitetura, autenticação (papéis,
convite, JWT), banco, Omie, sincronização, deploy, fluxo de trabalho,
glossário e registro de decisões. Leia a página relevante antes de mexer
numa área. Este arquivo guarda só como o agente deve trabalhar.

Wiki do GitHub: páginas soltas (sem subpastas), nome do arquivo = título
com hífens (`Banco-de-Dados.md`), links no formato `[[Nome da Página]]`.
Wiki não tem PR — edição é commit + push direto no repo da wiki (sempre
`git pull` antes).

## Papel do agente

Você atua como engenheiro(a) de software deste projeto junto com o usuário
(Levi, Tática Contabilidade).

- **Idioma:** toda conversa, texto voltado ao usuário, mensagem de erro,
  comentário de código, commit e documentação em **português (pt-BR)**.
  Nomes de domínio no código também em português (`conta_pagar`,
  `fluxo_caixa`), seguindo o padrão do `forms-meta`.
- **Registrar decisões na wiki.** Toda observação, decisão ou restrição
  que o usuário fizer entra na página certa da wiki e no
  `Registro-de-Decisões.md`, com data e motivo — não fica só na
  conversa. Decisão que mudou: atualize o tópico e registre a mudança; não
  deixe as duas versões convivendo. Itens em aberto ficam como `- [ ]` na
  página.
- **Decisão de infra/custo é do usuário.** Criar serviço/banco/cron no
  Render, contratar licença, mudar plano: proponha com custo e trade-off e
  espere confirmação. Nunca provisione nada sozinho.
- **Não toque no `forms-meta` sem pedido explícito.** Login, usuários e as
  telas do dashboard moram lá. Quando uma mudança lá for necessária,
  descreva o que muda e por quê, e só implemente quando o usuário pedir —
  seguindo o CLAUDE.md *daquele* repo, **exceto no fluxo de commit**: no
  forms-meta o merge também é só via PR, sem merge próprio, mesmo o
  CLAUDE.md de lá dizendo para mergear localmente — mas lá a base é a
  **`main`** (branch a partir da `main`, PR para a `main`), porque a `dev`
  do forms-meta está muito atrás da `main`. Decisão do usuário em
  2026-11-05; não atualizar a seção de fluxo do CLAUDE.md de lá por
  enquanto.
- **Dado financeiro é sensível.** Permissão sempre checada no servidor
  (nunca só escondendo botão no React); colaborador nunca alcança endpoint
  de escrita; nunca logue credenciais do Omie nem payloads financeiros.
  Ver os RNF de segurança em `Requisitos-Não-Funcionais.md` (wiki).
- **Nunca confie no cliente:** totais, saldos e agregados calculados no
  servidor.
- **Escrita no Omie é caso a caso.** Nenhuma operação que altera dado no
  Omie é implementada sem ter sido decidida e documentada em
  `Integração-Omie.md` (wiki).
- **Testes nunca chamam a API real do Omie** nem SMTP real.

## Fluxo de commit (resumo)

Detalhes em `Fluxo-de-Trabalho.md` (wiki). Só quando o usuário pedir:

1. `pytest` passando.
2. Branch a partir da `dev` atualizada (`<tipo>/<nome-curto>`).
3. Commits atômicos; conferir `git status` (nunca `.env`, `*.pdf`, prefixo
   `_`, material de referência solto).
4. `git push -u origin <branch>`.
5. `gh pr create --base dev`.
6. **Não mergiar por conta própria** — merge só via PR aprovado. Nunca
   push direto na `dev`/`main`, nunca burlar a proteção de branch.

Toda mudança também é registrada no `CHANGELOG.md` (por branch, com data).
