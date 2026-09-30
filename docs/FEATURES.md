# Feature Scope — TapJob

Legenda:
- **V1**: escopo de lançamento.
- **V1.x**: pós-lançamento próximo.
- **V2**: expansão.
- **Future**: não comprometer prazo inicial.

## 0. Product-scope boundary

The feature set is candidate-first and job-search execution focused.

Not planned as core product:
- public social feed;
- user posts;
- likes/comments/followers;
- creator/influencer mechanics;
- LinkedIn-style professional social graph;
- recruiter-side ATS/marketplace as the initial business.

The word "feed" in this document refers to job recommendations/opportunities.

Networking features are private candidate tools: recruiter/contact records, outreach drafts, referrals and follow-ups.

## 1. Career Profile

| Feature | Fase |
|---|---|
| Perfil profissional estruturado | V1 |
| Importar PDF/DOCX | V1 |
| Skill inventory | V1 |
| Preferências de cargo/local/salário | V1 |
| Respostas frequentes de candidatura | V1 |
| Professional Truth Store | V1 |
| Histórico de versões | V1.x |
| Importadores adicionais autorizados | V2 |

## 2. Jobs

| Feature | Fase |
|---|---|
| Feed personalizado | V1 |
| Busca/filtros | V1 |
| Favoritos | V1 |
| Watchlist de empresas | V1 |
| Deduplicação cross-source | V1 |
| Score e explicação | V1 |
| Hard blockers | V1 |
| Skills encontradas/faltantes | V1 |
| Alertas de novas vagas | V1 |
| Vagas similares | V1.x |
| Detecção de reposts/possível ghost job | V1.x |
| Salary intelligence | V2 |

## 3. Resume & ATS

| Feature | Fase |
|---|---|
| Resume Builder | V1 |
| Currículo mestre | V1 |
| Templates ATS-safe | V1 |
| PDF | V1 |
| DOCX | V1.x |
| ATS score | V1 |
| Keyword analysis | V1 |
| Tailor por vaga | V1 |
| Comparar versões | V1.x |
| Biblioteca de currículos | V1 |
| Auto seleção do melhor currículo-base | V1.x |

## 4. Application Kit

| Feature | Fase |
|---|---|
| Cover letter | V1 |
| Respostas sugeridas | V1 |
| Perguntas eliminatórias destacadas | V1 |
| Company brief | V1 |
| Mensagem para recrutador | V1 |
| Checklist | V1 |
| Package export/share | V1.x |

## 5. Apply Engine

| Feature | Fase |
|---|---|
| Deep link | V1 |
| Assisted apply básico | V1 |
| Browser extension | V1.x |
| Autofill profile | V1.x |
| Autofill resume/document | V1.x |
| Human review gate | V1.x |
| Official API connector | quando disponível |
| Auto-submit oficial | V2 / parceria |
| Universal headless auto-apply | Fora de escopo |

## 6. Tracking

| Feature | Fase |
|---|---|
| Kanban/pipeline | V1 |
| Timeline | V1 |
| Notas | V1 |
| Tasks | V1 |
| Follow-up reminders | V1 |
| Documentos vinculados | V1 |
| Contacts vinculados | V1.x |
| Inbox status sync | V1.x |

## 7. Inbox & Calendar

| Feature | Fase |
|---|---|
| Gmail OAuth | V1.x |
| Detectar confirmação | V1.x |
| Detectar assessment | V1.x |
| Detectar entrevista | V1.x |
| Detectar rejeição | V1.x |
| Detectar oferta | V1.x |
| Criar task/event | V1.x |
| Push de atualização | V1.x |
| Outlook | V2 |
| Calendar sync | V2 |

## 8. Interview

| Feature | Fase |
|---|---|
| Role/company prep | V1 |
| Perguntas prováveis | V1 |
| STAR story bank | V1 |
| Simulação texto | V1 |
| Debrief | V1 |
| Follow-up draft | V1 |
| Simulação por voz | V2 |
| Entrevista ao vivo/cue assist | Future, sujeito a políticas |

## 9. Career Agent

| Feature | Fase |
|---|---|
| Chat mobile | V1 |
| Ferramentas de perfil | V1 |
| Ferramentas de vagas | V1 |
| Ferramentas de currículo | V1 |
| Ferramentas de tracking | V1 |
| Confirmação antes de writes relevantes | V1 |
| Memória de carreira | V1 |
| WhatsApp | V1 |
| Resumo diário opcional | V1.x |
| Planejamento semanal | V1.x |
| Negociação salarial | V2 |

## 10. Mobile

Tabs propostas:
1. Home
2. Vagas
3. Aplicações
4. Agente
5. Perfil

Áreas secundárias:
- Currículos
- Entrevistas
- Documentos
- Analytics
- Contatos
- Configurações

## 11. Analytics

### V1
- total candidaturas;
- respostas;
- entrevistas;
- ofertas;
- rejeições;
- conversão básica;
- tempo médio por etapa.

### V1.x
- conversão por fonte;
- conversão por cargo;
- conversão por currículo;
- score vs avanço;
- weekly trend.

### V2
- cohort;
- experimentos de currículo;
- salary outcomes;
- recommendation calibration.

## 12. Brasil-first

Prioridades:
1. Gupy — parceria/API oficial como prioridade estratégica.
2. LinkedIn — descoberta/link e aplicação assistida até acesso oficial.
3. Indeed — descoberta/link e aplicação assistida até parceria.
4. Páginas das empresas.
5. Greenhouse / Lever / Ashby.
6. Workday / SmartRecruiters.
7. Pesquisa posterior: Sólides, Vagas.com.br, Catho, InfoJobs e demais fontes relevantes.

## 13. Planos comerciais — ainda não decididos

Possibilidades a validar:
- Free: perfil, tracking e limite de IA.
- Pro: tailoring, agente, alertas avançados, analytics.
- Plus/Automation: inbox, extension e mais automações.
- B2B2C/parcerias: universidades, bootcamps, outplacement, RH.

Nenhum pricing está aprovado.