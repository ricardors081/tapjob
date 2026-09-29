# Product Requirements Document — Tap Vaga

## 1. Objetivo

Entregar uma plataforma de carreira omnichannel que reduz o trabalho operacional da busca de emprego e aumenta a qualidade das candidaturas.

## 2. Personas

### P1 — Candidato ativo
Procura vagas diariamente, candidata-se em múltiplas plataformas e precisa de velocidade.

### P2 — Candidato em transição
Quer reposicionar carreira, precisa entender aderência, gaps e reescrever materiais.

### P3 — Candidato passivo
Está empregado, mas aceita oportunidades relevantes e prefere alertas de alta precisão.

### P4 — Primeiro emprego / estágio
Pouca experiência, precisa de orientação, currículo e preparação para entrevistas.

## 3. Jobs to be Done

- "Quando surgir uma vaga realmente boa para mim, quero saber rápido."
- "Quero entender se vale a pena me candidatar."
- "Quero um currículo adaptado sem mentir."
- "Quero perder menos tempo preenchendo os mesmos formulários."
- "Quero saber onde me candidatei e qual foi a resposta."
- "Quero chegar preparado à entrevista."
- "Quero entender por que minhas candidaturas não avançam."

## 4. Requisitos funcionais canônicos

### FR-001 Perfil profissional mestre
Armazenar:
- identidade e contatos;
- headline;
- resumo;
- experiências;
- projetos;
- formação;
- certificações;
- idiomas;
- skills;
- links;
- preferências de trabalho;
- salário;
- localizações;
- restrições;
- autorização de trabalho;
- respostas frequentes;
- fatos confirmados e sua proveniência.

### FR-002 Importação de currículo
Aceitar PDF/DOCX e extrair dados estruturados para revisão.

### FR-003 Professional Truth Store
Persistir fatos profissionais confirmados e impedir geração não fundamentada.

### FR-004 Descoberta de vagas
Suportar conectores plugáveis, scheduler, dedupe, normalização e origem rastreável.

### FR-005 Busca manual
Permitir pesquisa por cargo, local, modalidade, senioridade, salário, empresa e keywords.

### FR-006 Matching
Aplicar:
1. hard filters;
2. lexical/semantic match;
3. LLM reasoning opcional;
4. score final;
5. explicação;
6. requisitos faltantes;
7. risco/alertas.

### FR-007 Feed personalizado
Mostrar vagas ordenadas por relevância e freshness.

### FR-008 Watchlists
Seguir empresas, cargos, termos e localidades.

### FR-009 Resume Studio
Criar/editar currículo mestre, templates e versões.

### FR-010 ATS analysis
Avaliar legibilidade, estrutura, keywords, match com JD e problemas de formatação.

### FR-011 Tailored resume
Gerar versão por vaga somente com fatos autorizados.

### FR-012 Cover letter
Gerar carta específica por vaga.

### FR-013 Application answers
Manter biblioteca de respostas e gerar sugestões para perguntas abertas.

### FR-014 Application Kit
Agrupar:
- currículo;
- cover letter;
- respostas;
- resumo da vaga;
- company brief;
- mensagem para recrutador;
- checklist.

### FR-015 Apply Engine
Selecionar automaticamente o modo:
- OFFICIAL_API;
- ASSISTED_EXTENSION;
- DEEP_LINK;
- MANUAL;
- DISABLED.

### FR-016 Human approval
Permitir configuração de níveis:
- sempre revisar;
- revisar apenas dados não triviais;
- auto-submeter somente conectores autorizados e regras elegíveis.

### FR-017 Application Tracker
Estados mínimos:
Discovered -> Saved -> Preparing -> Ready -> Applied -> Assessment -> Interview -> Offer -> Hired / Rejected / Withdrawn / Expired.

### FR-018 Timeline
Registrar eventos, documentos, mensagens, mudanças de status e ações.

### FR-019 Inbox Intelligence
Com consentimento, interpretar e-mails de candidatura, assessment, entrevista, rejeição e oferta.

### FR-020 Calendar
Criar/associar entrevistas, avaliações, deadlines e follow-ups.

### FR-021 Notifications
Push/app/WhatsApp/e-mail com preferências e throttling.

### FR-022 Interview Prep
Gerar:
- company brief;
- role brief;
- perguntas prováveis;
- respostas STAR;
- pontos de atenção;
- perguntas para entrevistador.

### FR-023 Interview Simulator
Simulação por texto e futuramente voz.

### FR-024 Interview Debrief
Registrar perguntas recebidas, percepção, próximos passos e follow-up.

### FR-025 Networking
Gerenciar recrutadores, hiring managers, referências e contatos.

### FR-026 Outreach
Gerar rascunhos para LinkedIn/e-mail/WhatsApp sem envio não autorizado.

### FR-027 Follow-up
Lembretes e rascunhos após candidatura/entrevista.

### FR-028 Career Agent
Chat com ferramentas, memória profissional e capacidade de executar ações confirmadas.

### FR-029 WhatsApp Agent
Mesmo Career Agent, com comandos naturais e mensagens proativas permitidas.

### FR-030 Mobile App
Career Hub completo.

### FR-031 Browser Extension
Mapear formulários e preencher com dados aprovados.

### FR-032 Analytics
Funil, taxa de resposta, entrevistas, ofertas e conversão por origem/currículo/role.

### FR-033 Rejection patterns
Detectar padrões sem alegar causalidade sem evidência.

### FR-034 Data portability
Exportar perfil, currículos, histórico e candidaturas.

### FR-035 Account deletion
Apagar conta e dados conforme política e obrigações legais.

## 5. Requisitos não funcionais

### NFR-001 Segurança
Nenhum segredo no cliente. Tokens cifrados. RBAC/admin separado.

### NFR-002 Privacidade
Consentimento, minimização, retenção e finalidade explícita.

### NFR-003 Auditabilidade
Ações automatizadas relevantes devem registrar quem/o que iniciou, input, resultado e decisão.

### NFR-004 Idempotência
Ingestão, notificações e submissões devem tolerar retries sem duplicar efeitos.

### NFR-005 Observabilidade
Logs estruturados, métricas, tracing de jobs e filas.

### NFR-006 Resiliência
Falha de um conector não derruba o Career Core.

### NFR-007 Portabilidade
Conectores e provedores de IA atrás de interfaces.

### NFR-008 Latência
Operações síncronas de UI devem permanecer responsivas; tarefas longas vão para fila.

### NFR-009 Acessibilidade
WCAG como referência para web e boas práticas nativas no mobile.

### NFR-010 Internacionalização
pt-BR primeiro; strings externalizadas desde V1.

## 6. Regras de negócio críticas

- Não inventar experiência ou skill.
- Não aplicar em vaga com hard blocker sem confirmação explícita.
- Não usar dados demográficos/sensíveis para aumentar score de matching.
- Não responder perguntas legais/EEO sem dados confirmados.
- Não submeter automaticamente onde integração não for autorizada.
- Toda vaga deve preservar URL/origem externa.
- Toda candidatura deve ter idempotency key por usuário + vaga + conector.
- Toda versão de currículo deve registrar vaga-alvo e versão do perfil usada.
- O usuário pode desativar qualquer canal proativo.

## 7. Critérios de aceite de V1

V1 só pode ser considerada pronta quando um usuário conseguir:
1. criar/importar perfil;
2. receber vagas relevantes;
3. abrir explicação do match;
4. gerar currículo adaptado;
5. gerar application kit;
6. concluir candidatura via um dos modos suportados;
7. acompanhar status;
8. receber pelo menos um tipo de atualização automática;
9. preparar uma entrevista;
10. consultar tudo pelo app e interagir com o agente.
