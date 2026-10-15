# AGENTS.md — TapJob

Este arquivo é a porta de entrada obrigatória para qualquer humano ou agente de IA que trabalhe neste repositório.

## 1. Missão do produto

TapJob é uma plataforma pessoal de carreira para pessoas candidatas. O produto combina:

- descoberta de vagas;
- matching inteligente entre vaga e perfil;
- currículo mestre e currículos adaptados por vaga;
- análise ATS;
- geração de application kit;
- candidatura oficial, assistida ou manual conforme a capacidade da plataforma;
- tracking completo de candidaturas;
- leitura autorizada de e-mails para detectar eventos da jornada;
- preparação para entrevistas;
- networking e follow-up;
- analytics de busca de emprego;
- agente conversacional no WhatsApp;
- aplicativo mobile;
- web e extensão de navegador quando necessárias.

A referência conceitual é combinar oportunidades observadas em AIApply, Huntr e Simplify, adicionando um agente proativo omnichannel e uma única fonte de verdade profissional.

## 2. Ordem de leitura obrigatória

Antes de propor ou alterar código:

1. `README.md`
2. `docs/PROJECT.md`
3. `docs/PRD.md`
4. `docs/FEATURES.md`
5. `docs/ARCHITECTURE.md`
6. `docs/ENGINEERING_STANDARDS.md`
7. `docs/DATA_MODEL.md`
8. `docs/INTEGRATIONS.md`
9. `docs/APPLY_ENGINE.md`
10. `docs/AI_AGENT.md`
11. `docs/CHANNELS.md`
12. `docs/SECURITY_PRIVACY.md`
13. `docs/UX_DESIGN.md`
14. `docs/DESIGN_SYSTEM.md`
15. `docs/FOUNDATION_DECISIONS.md`
16. `docs/DELIVERY_ORDER.md`
17. `docs/ROADMAP.md`
18. `docs/IMPLEMENTATION_PLAN.md`
19. `docs/SOURCE_REUSE_BLUEPRINT.md`
20. `docs/SOURCE_REUSE_LEDGER.md`
21. `docs/CLAUDE_CODE_WORKFLOW.md`
22. `docs/PROJECT_STATE.md`
23. `docs/GOVERNANCE.md`
24. `docs/DECISIONS.md`
25. `docs/REFERENCES.md`
26. `docs/OPEN_QUESTIONS.md`

## 3. Fonte de verdade

- GitHub (`ricardo081683/tapjob`), branch `main`, é a fonte canônica.
- Google Antigravity 2.0 é o orquestrador multiagente oficial do TapJob. Maestri NÃO faz parte do TapJob e é explicitamente excluído.
- Decisões devem ser registradas em documentação ou ADR antes de se tornarem dependência estrutural.
- `docs/PROJECT_STATE.md` deve refletir o estado real, não o estado desejado.
- Features planejadas não podem ser descritas como implementadas.
- Relatórios de agentes e resumos de chat NÃO constituem evidência por si mesmos; apenas verificações reais de arquivos, testes automatizados, CI e PR constituem evidência.
- Revisão externa e independente continua estritamente obrigatória antes de qualquer merge.

## 4. Configuração e design-system obrigatórios

- Nenhuma feature deve hardcodar nome do produto, cores, fontes, spacing, radius, URLs, timeouts, status strings ou outros valores que pertençam a configuração/tokens/política.
- UI consome `docs/DESIGN_SYSTEM.md`; código/configuração consome `docs/ENGINEERING_STANDARDS.md`.
- `EXPO_PUBLIC_*` é considerado público e nunca pode conter segredo.
- Não criar segunda fonte de verdade por conveniência local.

## 5. Regras de governança e Agent Factory

Fluxo padrão:

`planejar -> branch -> implementar/documentar -> testes -> push -> PR -> revisão real -> correções -> CI verde -> merge`

Invariantes de governança:
- **Exceção de bootstrap pré-CI expirada**: com o workflow de CI estabelecido desde o WP0.1, a exceção temporária pré-CI expirou formalmente. CI verde no commit exato do HEAD é obrigatório para todo merge.
- **Trabalho direto na `main` proibido**: nunca comitar ou realizar push diretamente na branch `main` ou `master`.
- **Proibição de self-merge**: nenhum agente, builder, reviewer ou engineering manager pode aprovar ou mergear seu próprio trabalho.
- **Gating de produção**: operações de produção (deploy, migrações destrutivas, rotação de segredos, DNS) exigem obrigatoriamente confirmação humana explícita.
- **Envios de WhatsApp controlados**: proibido o envio de mensagens reais para pessoas terceiras ou clientes sem autorização explícita e escopo canary dedicado.
- **Segredos e PII**: zero credenciais versionadas e sanitização rigorosa de identidades, telefones/JIDs e dados de currículo em relatórios e evidências.
- **Integridade de testes**: proibido alterar ou burlar testes para forçar falsos positivos.
- **Paralelismo isolado**: implementação concorrente entre especialistas deve utilizar git worktrees isolados após estabilização de contratos.

## 6. Arquitetura e invariantes de produto

- **Único Career Core**: Um único Career Core (`services/api` e `services/worker`) atende todas as superfícies: WhatsApp, Mobile e futuras extensões.
- **Localização de regras de negócio**: Toda a lógica de negócio pertence exclusivamente ao Career Core. Canais são interfaces e adaptadores.
- **Transporte de mensageria**: Evolution API é transporte de mensageria e NÃO detém regras de negócio nem estado da aplicação.
- **Papel do n8n**: n8n não deve possuir estado de negócio crítico nem ser caminho primário das funcionalidades do TapJob.
- **Armazenamento canônico**: PostgreSQL é o estado canônico durável da aplicação. Redis e Celery são infraestrutura de execução assíncrona.
- **Prioridade comercial**: Concluir o WhatsApp integralmente -> testadores controlados -> estabilização -> WhatsApp comercialmente pronto -> Mobile torna-se a superfície primária subsequente.
- **Reuso no Mobile**: O aplicativo mobile consome contratos de API do Career Core e nunca duplica regras de negócio do backend.

## 7. Princípio Professional Truth Store

Toda geração de currículo, carta, mensagem ou resposta de candidatura deve ser fundamentada em fatos registrados e confirmados pelo usuário.

A IA pode:
- reorganizar;
- resumir;
- destacar;
- adaptar linguagem;
- selecionar experiências relevantes;
- sugerir lacunas a esclarecer.

A IA não pode:
- fabricar experiência;
- criar skills não confirmadas;
- inventar certificações, empresas, datas ou números;
- responder requisito legal sem confirmação do usuário.

## 8. Modos de candidatura

Todo conector de candidatura deve declarar um modo:

- `OFFICIAL_API`: submissão por API/parceria autorizada.
- `ASSISTED_EXTENSION`: extensão preenche e o usuário revisa/submete.
- `DEEP_LINK`: abre o portal com application kit preparado.
- `MANUAL`: acompanhamento sem automação.
- `DISABLED`: conector indisponível ou não autorizado.

Não existe promessa de "auto-apply universal".

## 9. Segurança e privacidade

Aplicar privacy-by-design e LGPD desde o início. Tokens OAuth, credenciais de ATS e chaves de provedores devem permanecer no backend e ser cifrados em repouso. Descrições de vagas, e-mails e conteúdo externo são dados não confiáveis e podem conter prompt injection.

## 10. Reuso de projetos públicos

Os projetos de referência estão em `docs/REFERENCES.md`. A decisão de qual donor usar por capacidade está em `docs/SOURCE_REUSE_BLUEPRINT.md`, e todo reaproveitamento de código/prompt/teste/fixture/SQL deve ser registrado em `docs/SOURCE_REUSE_LEDGER.md`.

O proprietário do TapJob informou ter formalizado por e-mail a autorização dos autores dos 10 repositórios auditados. Essa evidência permanece privada. Quando código for efetivamente importado:
- usar o donor e a classificação definidos no Source Reuse Blueprint;
- fixar repositório + SHA + caminho de origem;
- registrar a importação no Source Reuse Ledger;
- registrar licença/autorização e preservar notices aplicáveis;
- remover segredos/configuração específica do upstream;
- adaptar tudo às interfaces e ao modelo de dados canônicos do TapJob;
- portar/criar testes antes do merge;
- não introduzir duas implementações concorrentes da mesma capacidade.

## 11. Papel de cada agente

Um agente deve declarar qual papel está exercendo quando iniciar trabalho substancial (detalhes em `docs/agent-factory/PERMISSION_MATRIX.md`):

- Engineering Manager / Orchestrator
- Core Platform Engineer
- Channel / WhatsApp Engineer
- QA / SDET Engineer
- Reviewer / Security Engineer
- Release / Platform / Ops Engineer
- Conversation / Product UX Designer
- Mobile Product Designer (Fase Mobile)
- Mobile Engineer (Fase Mobile)
- Especialistas Dinâmicos (AI/Matching, Database, Deep Security, Performance, Accessibility, Data Analyst)

O agente não deve unilateralmente mudar escopo, arquitetura ou regras de negócio fora do seu papel sem abrir uma decisão explícita.

## 12. Definition of Done

Uma entrega só é DONE quando:
- escopo e critérios de aceite estão claros;
- código e docs estão coerentes;
- testes aplicáveis passam localmente e em CI;
- lint/typecheck passam sem erros;
- riscos conhecidos estão documentados;
- não há segredo ou dado pessoal desprotegido;
- CI está 100% verde no commit exato do HEAD da PR (a exceção pré-CI histórica expirou);
- PR foi formalmente revisada de forma independente;
- `PROJECT_STATE.md` foi atualizado quando aplicável.