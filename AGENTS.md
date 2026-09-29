# AGENTS.md — Tap Vaga

Este arquivo é a porta de entrada obrigatória para qualquer humano ou agente de IA que trabalhe neste repositório.

## 1. Missão do produto

Tap Vaga é uma plataforma pessoal de carreira para pessoas candidatas. O produto combina:

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
6. `docs/DATA_MODEL.md`
7. `docs/INTEGRATIONS.md`
8. `docs/APPLY_ENGINE.md`
9. `docs/AI_AGENT.md`
10. `docs/CHANNELS.md`
11. `docs/SECURITY_PRIVACY.md`
12. `docs/UX_DESIGN.md`
13. `docs/ROADMAP.md`
14. `docs/IMPLEMENTATION_PLAN.md`
15. `docs/PROJECT_STATE.md`
16. `docs/GOVERNANCE.md`
17. `docs/DECISIONS.md`
18. `docs/REFERENCES.md`
19. `docs/OPEN_QUESTIONS.md`

## 3. Fonte de verdade

- GitHub é a fonte canônica.
- Decisões devem ser registradas em documentação ou ADR antes de se tornarem dependência estrutural.
- `docs/PROJECT_STATE.md` deve refletir o estado real, não o estado desejado.
- Features planejadas não podem ser descritas como implementadas.
- Relatórios de agentes não substituem verificação de arquivos, testes, CI e PR.

## 4. Regras de governança

Fluxo padrão:

`planejar -> branch -> implementar/documentar -> testes -> push -> PR -> revisão real -> correções -> CI verde -> merge`

Existe uma única exceção de bootstrap pré-CI: enquanto o repositório ainda não possui workflow de CI configurado, uma PR exclusivamente documental/de fundação pode ser mergeada após revisão real do diff, confirmação de que não existem checks configurados e registro explícito dessa exceção. Após o WP0.1 instalar CI, essa exceção deixa de existir.

Nunca:

- alterar `main` diretamente, salvo bootstrap técnico inevitável;
- marcar trabalho como concluído sem evidência;
- ocultar riscos, limitações de API ou dependências externas;
- introduzir credenciais em código;
- expor chaves de IA em aplicativos cliente;
- automatizar submissão em plataforma fechada sem base técnica/contratual aprovada;
- inventar experiência, competências, formação ou resultados no currículo do usuário.

## 5. Princípio Professional Truth Store

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

## 6. Modos de candidatura

Todo conector de candidatura deve declarar um modo:

- `OFFICIAL_API`: submissão por API/parceria autorizada.
- `ASSISTED_EXTENSION`: extensão preenche e o usuário revisa/submete.
- `DEEP_LINK`: abre o portal com application kit preparado.
- `MANUAL`: acompanhamento sem automação.
- `DISABLED`: conector indisponível ou não autorizado.

Não existe promessa de "auto-apply universal".

## 7. Segurança e privacidade

Aplicar privacy-by-design e LGPD desde o início. Tokens OAuth, credenciais de ATS e chaves de provedores devem permanecer no backend e ser cifrados em repouso. Descrições de vagas, e-mails e conteúdo externo são dados não confiáveis e podem conter prompt injection.

## 8. Reuso de projetos públicos

Os projetos de referência e seus usos estão em `docs/REFERENCES.md`.

O proprietário do Tap Vaga informou ter obtido autorização dos autores para reaproveitamento. Quando código for efetivamente importado:
- registrar origem;
- registrar commit/tag de origem;
- registrar licença/autor;
- preservar notices necessários;
- preferir adaptação modular em vez de copiar blocos sem rastreabilidade.

## 9. Papel de cada agente

Um agente deve declarar qual papel está exercendo quando iniciar trabalho substancial:

- Product Manager
- Tech Lead / Architect
- Backend
- Mobile
- Web/Extension
- AI/Agents
- Integrations
- Security/Privacy
- QA
- Design System
- DevOps/SRE
- Reviewer

O agente não deve unilateralmente mudar escopo, arquitetura ou regras de negócio fora do seu papel sem abrir uma decisão explícita.

## 10. Definition of Done

Uma entrega só é DONE quando:
- escopo e critérios de aceite estão claros;
- código e docs estão coerentes;
- testes aplicáveis passam;
- lint/typecheck passam;
- riscos conhecidos estão documentados;
- não há segredo hardcoded;
- CI está verde quando CI já estiver configurado; a exceção pré-CI é somente a descrita em `docs/GOVERNANCE.md`;
- PR foi revisada;
- `PROJECT_STATE.md` foi atualizado quando aplicável.
