# TapJob

> Plataforma pessoal de carreira para descobrir vagas, preparar candidaturas, aplicar com o nível de automação permitido, acompanhar processos e evoluir até a contratação.

**Status atual:** Foundation / Phase 0 **verificado** (WP0.1 verificado; WP0.2 verificado — WP0.2-A, WP0.2-B Checkpoint A e WP0.2-B Checkpoint B todos verificados). Phase 1 — Career Core checkpoints: **WP1.1 — Identity & User Account VERIFICADO** (PR #17, merge squash `bc269d8dd99f1455d1d44cf8b3abb4ba8d7915e7`, CI verde pré e pós-merge — identidade interna `User` UUID canônica; identidade externa Firebase via `ExternalIdentity`, nunca auto-linkada por e-mail; bootstrap concurrency-safe; verificação de token com `check_revoked=True`; revoke-all, devices, consentimento append-only, esqueleto de exclusão de conta; rate limiting fail-closed; mobile em React Native Firebase + Expo Development Build — ver `docs/adr/0009-identity-auth-user-account.md`, D-051). **WP1.2 — Career Profile & Professional Truth Store VERIFICADO** (PR #19, merge squash `5c06c805ea2a68fcf100ec56cff5eaf715e962c2`; defeito real de concorrência corrigido pela PR corretiva #20, merge squash `f30590577cf25733de63025f987c251ebe0e89af`, com CI pós-merge verde 8/8 — ver `docs/adr/0010-career-profile-truth-store.md`, D-052). **WP1.3 — Resume Import VERIFICADO** (PR #22, merge squash `db1754acf2a6aabb4b13df3a7de35a8a0d795a48`, HEAD aprovado `fba5765a291abc0c9bfea548c6f97f7bf92e0db0`, CI pré-merge run `37538366658` 8/8 verde, CI pós-merge run `37551974662` 8/8 verde, equivalência de árvore confirmada; duas rodadas de revisão independente com 10 + 9 achados/itens de hardening resolvidos: upload PDF/DOCX com validação por assinatura/container, armazenamento privado em R2, extração limitada sem OCR, parser determinístico sem IA, validação fail-closed contra `FactValue`, revisão explícita/finalização, criação de currículo mestre, leasing/fencing atômico com token, varredura de dispatch durável, integridade de objeto R2; ciclo de vida de retenção/exclusão de blobs brutos permanece como gate pré-produção — ver `docs/adr/0011-resume-import.md`, D-053). Próximo trabalho de domínio: Phase 2 — Jobs & Matching (WP2.1 em análise de decision gate). WP2.2+ permanecem não autorizados. Nenhuma conta real, nenhum deploy real, nenhuma mutação em Firebase Console/Google Console/Apple Developer, nenhuma migration real executada até o momento — a primeira Development Build real, a configuração real do Firebase/Google/Apple, a migração de ambiente real e o ciclo de vida completo de deleção de blobs são pré-requisitos operacionais e gates pendentes.  
**Mercado inicial:** Brasil.  
**Aplicação em produção:** ainda não existe.

## Visão

TapJob combina oportunidades de produto observadas em AIApply, Huntr e Simplify com:

- aplicativo mobile;
- agente no WhatsApp;
- Career Agent com memória profissional;
- descoberta e matching de vagas;
- currículo ATS e tailoring por vaga;
- application kits;
- candidatura oficial/assistida/deep-link conforme cada plataforma;
- tracking;
- inbox intelligence;
- entrevistas;
- analytics.

## Comece aqui

Se você é um agente ou desenvolvedor novo:

1. Claude Code: comece por [CLAUDE.md](CLAUDE.md); demais agentes/humanos: comece por [AGENTS.md](AGENTS.md);
2. leia [docs/PROJECT.md](docs/PROJECT.md);
3. leia [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md);
4. siga [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md);
5. respeite [docs/GOVERNANCE.md](docs/GOVERNANCE.md).

## Documentação canônica

- [Project Charter](docs/PROJECT.md)
- [PRD](docs/PRD.md)
- [Feature Scope](docs/FEATURES.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Domain/Data Model](docs/DATA_MODEL.md)
- [Integrations](docs/INTEGRATIONS.md)
- [Apply Engine](docs/APPLY_ENGINE.md)
- [Career Agent](docs/AI_AGENT.md)
- [Product Surfaces](docs/CHANNELS.md)
- [Security & Privacy](docs/SECURITY_PRIVACY.md)
- [UX & Design](docs/UX_DESIGN.md)
- [Design System & Tokens](docs/DESIGN_SYSTEM.md)
- [Engineering Standards](docs/ENGINEERING_STANDARDS.md)
- [Delivery Order](docs/DELIVERY_ORDER.md)
- [Roadmap](docs/ROADMAP.md)
- [Foundation Decisions](docs/FOUNDATION_DECISIONS.md)
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
- [Source Reuse Blueprint](docs/SOURCE_REUSE_BLUEPRINT.md)
- [Source Reuse Ledger](docs/SOURCE_REUSE_LEDGER.md)
- [Claude Code Workflow](docs/CLAUDE_CODE_WORKFLOW.md)
- [Project State](docs/PROJECT_STATE.md)
- [Governance](docs/GOVERNANCE.md)
- [Decision Register](docs/DECISIONS.md)
- [Reference Projects](docs/REFERENCES.md)
- [Open Questions](docs/OPEN_QUESTIONS.md)

## Regra central

Não existe promessa de auto-apply universal.

Cada candidatura usa um dos modos:

`OFFICIAL_API -> ASSISTED_EXTENSION -> DEEP_LINK -> MANUAL -> DISABLED`

A IA nunca deve inventar experiência, skill, formação ou resposta legal. O **Professional Truth Store** é a fonte de verdade para materiais profissionais.

## Estrutura do repositório

```text
apps/mobile/              app Expo (shell mínimo: router, tokens, product config, i18n, config pública)
services/api/             backend FastAPI (Career Core), settings tipados, readiness, Alembic
services/worker/          worker Celery (mesmo código do backend)
packages/product-config/  identidade do produto (product.json)
packages/design-tokens/   tokens DTCG + saída gerada
packages/contracts/       cliente TypeScript gerado do contrato OpenAPI (WP0.2-A)
infra/                    Dockerfile e Docker Compose local
docs/                     documentação canônica e ADRs
```

## Desenvolvimento local

Pré-requisitos: Git, [Node.js](https://nodejs.org) (versão em `.nvmrc`), [uv](https://docs.astral.sh/uv/) e Docker (Docker Desktop no Windows). Funciona no Windows (PowerShell) e no Linux.

```bash
# dependências (lockfiles obrigatórios)
uv sync --locked --all-packages
npm ci

# todas as checagens locais (lint, format, tipos, testes, tokens)
npm run check

# stack local: postgres, redis, migrate, api, worker
cp infra/.env.example infra/.env        # PowerShell: Copy-Item infra/.env.example infra/.env
docker compose -f infra/compose.yaml up --build
curl http://127.0.0.1:8000/health       # liveness
curl http://127.0.0.1:8000/ready        # readiness: PostgreSQL + Redis

# app mobile (shell mínimo; nenhuma tela de produto ainda)
cp apps/mobile/.env.example apps/mobile/.env   # PowerShell: Copy-Item apps/mobile/.env.example apps/mobile/.env
npm run start --workspace @tapjob/mobile
```

Outros comandos:

- `npm run tokens:build`: regenera `packages/design-tokens/generated/native.ts` depois de editar os tokens;
- `npm run lint` / `npm run format:check`: ESLint (flat config + guards de magic value) e Prettier em `apps/` e `packages/`;
- `npx expo-doctor` / `npx expo export --platform android --platform ios` (a partir de `apps/mobile`): diagnóstico de dependências e validação de bundle, como no CI;
- `uv run pytest -m integration`: migrações e readiness contra PostgreSQL/Redis reais (exige `APP_ENV`, `DATABASE_URL` e `REDIS_URL`);
- o worker Celery roda via Docker (Celery não suporta Windows nativamente).

Toda configuração vem de variáveis de ambiente validadas na inicialização. Só arquivos `*.env.example` são versionados, e nunca contêm segredos.

## Estado

Consulte sempre [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) antes de assumir que algo está implementado.