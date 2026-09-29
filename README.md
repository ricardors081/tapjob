# Tap Vaga

> Plataforma pessoal de carreira para descobrir vagas, preparar candidaturas, aplicar com o nível de automação permitido, acompanhar processos e evoluir até a contratação.

**Status atual:** Foundation / documentação canônica.  
**Mercado inicial:** Brasil.  
**Aplicação em produção:** ainda não existe.

## Visão

Tap Vaga combina oportunidades de produto observadas em AIApply, Huntr e Simplify com:

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

1. leia [AGENTS.md](AGENTS.md);
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
- [Roadmap](docs/ROADMAP.md)
- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md)
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

## Estado

Consulte sempre [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) antes de assumir que algo está implementado.
