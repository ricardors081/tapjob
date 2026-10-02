# TapJob

> Plataforma pessoal de carreira para descobrir vagas, preparar candidaturas, aplicar com o nível de automação permitido, acompanhar processos e evoluir até a contratação.

**Status atual:** Foundation: WP0.1 verificado (monorepo, backend/worker skeletons, CI, Docker/Compose, shell Expo, readiness, observabilidade, i18n e design-token guards). WP0.2-A verificado (contrato HTTP/OpenAPI, cliente TypeScript gerado, CI de contrato). WP0.2-B (ports internos/capability model) é o próximo trabalho autorizado. Sem funcionalidades de produto.  
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