# WP-PILOT-2 — WhatsApp Product Journey Matrix

Esta matriz define formalmente os 22 fluxos essenciais de produto que uma pessoa candidata deve conseguir completar pelo WhatsApp em linguagem natural, sem obrigatoriedade de comandos rígidos ou frases exatas.

Cada fluxo é especificado no formato padrão:
`INPUT HUMANO → INTENÇÃO ESPERADA → CONTEXTO NECESSÁRIO → AÇÃO → RESPOSTA ESPERADA → EVIDÊNCIA → STATUS`.

---

## Matriz de Jornadas do Produto (22 Fluxos)

### J-01: Primeiro Contato
- **Input Humano:** "Olá", "Oi, tudo bem?", "Boa tarde, como funciona?"
- **Intenção Esperada:** `GREETING_FIRST_CONTACT`
- **Contexto Necessário:** Usuário não cadastrado previamente no canal (ou sem vínculo ativo).
- **Ação:** Criação atômica de `User` e `ChannelIdentityLink`, despacho da mensagem de boas-vindas com apresentação da proposta de valor e aviso ativo de privacidade LGPD.
- **Resposta Esperada:** Boas-vindas ao TapJob, explicação de que é um assistente pessoal de carreira, convite para enviar currículo em PDF ou texto, e orientação sobre privacidade (`privacidade`).
- **Evidência:** Envelope processado em `provider_event_envelopes`, link em `channel_identity_links`, mensagem na `outbound_message_queue` com prefixo `first_contact_`.
- **Status:** PASS

### J-02: Envio de Currículo PDF
- **Input Humano:** Documento anexo (MIME: `application/pdf`, nome: `curriculo_joao.pdf`).
- **Intenção Esperada:** `RESUME_DOCUMENT_INTAKE`
- **Contexto Necessário:** Usuário ativo; canal com suporte a download de mídia.
- **Ação:** Download do documento via adapter de mídia, persistência segura no Object Storage (R2/S3), extração de texto, parsing de fatos profissionais (competências, cargos, sumário) e criação de `ResumeImport` no estado `READY_FOR_REVIEW`.
- **Resposta Esperada:** Confirmação do recebimento do arquivo, quantidade de fatos/competências extraídos preliminarmente e instruções claras para confirmação ("confirmar todos", "rejeitar fato N", "editar fato N: valor").
- **Evidência:** `ResumeImport` com `status = 'ready_for_review'`, fatos criados em `candidate_facts`, outbox com chave `doc_...`.
- **Status:** PASS

### J-03: Envio de Experiência em Texto
- **Input Humano:** "Tenho 6 anos de experiência como Desenvolvedor Backend Python e Go. Trabalho na Empresa Alpha atuando com FastAPI, Docker, PostgreSQL e microsserviços."
- **Intenção Esperada:** `RESUME_TEXT_INTAKE`
- **Contexto Necessário:** Usuário ativo querendo cadastrar ou enriquecer perfil via texto livre sem PDF.
- **Ação:** Extração inteligente de competências e experiências profissionais a partir do texto livre, estruturação de fatos profissionais preliminares e criação/atualização de perfil de carreira sob o Professional Truth Store.
- **Resposta Esperada:** Reconhecimento das experiências e competências identificadas no texto, exibição do resumo estruturado e solicitação de confirmação para salvar no perfil.
- **Evidência:** Fatos gerados no banco de dados vinculados ao usuário, resposta informativa listando as habilidades reconhecidas.
- **Status:** PASS (Hardenizado no WP-PILOT-2)

### J-04: Criação / Atualização de Perfil
- **Input Humano:** "Pode atualizar meu perfil com essas informações", "salvar meus dados", "atualizar meu currículo".
- **Intenção Esperada:** `PROFILE_UPDATE_REQUEST`
- **Contexto Necessário:** Candidato com dados enviados ou pendentes de confirmação.
- **Ação:** Consolidação dos fatos aceitos no perfil profissional canônico (`ResumeVersion`), incrementando a versão do perfil (`profile_version`).
- **Resposta Esperada:** Confirmação da atualização do perfil com o número da nova versão e convite para enviar vagas para análise de match.
- **Evidência:** `ResumeVersion` gerada e persistida, contadores de fatos confirmados atualizados.
- **Status:** PASS

### J-05: Correção de Fatos
- **Input Humano:** "O fato 2 tá errado, pode tirar?", "rejeita a 1", "muda o fato 3 para Python Tech Lead", "tira o segundo item".
- **Intenção Esperada:** `FACT_EDIT_OR_REJECT`
- **Contexto Necessário:** `ResumeImport` ativo no estado `READY_FOR_REVIEW` com lista de itens numerados.
- **Ação:** Registro de rascunho de decisão (`CandidateReviewDraft`) marcando o item como rejeitado ou com valor editado (`EDIT_ACCEPT`).
- **Resposta Esperada:** Confirmação da alteração específica realizada (ex: "Fato 2 rejeitado com sucesso") e relatório dos fatos ainda pendentes de decisão.
- **Evidência:**
  1. Conversacional e rascunhos: `services/api/tests/messaging/test_pilot_flow.py::test_j05_single_fact_rejection_and_confirmation_cascade` e `services/api/tests/messaging/test_candidate_review_drafts.py`.
  2. Persistência real em banco de dados isolado (sem mocks do finalizador): `services/api/tests/messaging/test_pilot_flow.py::test_j05_real_database_fact_rejection_and_confirmation_persistence`. Comprova que a confirmação via `confirm_candidates_and_finalize` -> `resume_import_service.finalize_resume_import` e `career_profile_service.bump_profile_version` produz exatamente quatro fatos ativos em `profile_facts`, avança a versão do perfil para 1 apenas na confirmação final, e mantém o fato 2 com `review_state == 'rejected'` sem nunca ativá-lo no Truth Store.
  3. Recuperação de crash e idempotência em CI com PostgreSQL: `services/api/tests/messaging/test_ingress_e2e_canary.py::test_finalize_review_crash_between_domain_commit_and_effect_completion_postgres`.
- **Status:** PASS (Comprovação ponta a ponta com persistência real em banco isolado e integração CI)

### J-06: Envio de Link de Vaga
- **Input Humano:** "Olha essa vaga aqui: https://boards.greenhouse.io/empresa/jobs/123456", "o que acha desse link https://vagas.com/exemplo?"
- **Intenção Esperada:** `JOB_URL_INTAKE`
- **Contexto Necessário:** Usuário ativo; URL acessível.
- **Ação:** Extração de metadados da vaga (título, empresa, requisitos), ingestão no Career Core e cálculo automático de compatibilidade (Match Score WP2.2).
- **Resposta Esperada:** Título da oportunidade, empresa, pontuação de compatibilidade (Match Score XX/100, elegibilidade) e convite para candidatar-se ou gerar o Application Kit.
- **Evidência:** Registro em `jobs`, `job_match_scores`, mensagem outbox com chave `job_url_...`.
- **Status:** PASS

### J-07: Envio de Descrição de Vaga (Texto Colado)
- **Input Humano:** "Vaga: Engenheiro de Software Sênior\nEmpresa: Tech Solutions\nRequisitos: Python, AWS, Docker, Kubernetes, liderança técnica\nBenefícios: Remoto, VR, Plano de Saúde"
- **Intenção Esperada:** `JOB_TEXT_INTAKE`
- **Contexto Necessário:** Texto contendo termos característicos de oportunidade de emprego.
- **Ação:** Parsing do texto da vaga, extração de requisitos e cálculo de compatibilidade contra o perfil profissional do candidato.
- **Resposta Esperada:** Reconhecimento da oportunidade com título e empresa identificados, nota de match (XX/100) e recomendação de próximos passos.
- **Evidência:** Vaga registrada em `jobs` com proveniência de texto, score persistido.
- **Status:** PASS

### J-08: Análise e Match
- **Input Humano:** "Analisa essa vaga pra mim", "o que você acha dessa vaga?", "vale a pena me candidatar?", "qual o meu match nela?", "tenho chances?"
- **Intenção Esperada:** `MATCH_EXPLANATION_REQUEST`
- **Contexto Necessário:** Pelo menos uma vaga analisada ou enviada recentemente na conversa.
- **Ação:** Chamada à ferramenta de explicabilidade de match (`tool_explain_match`), discriminando pontos fortes (habilidades correspondentes) e requisitos pendentes (gaps).
- **Resposta Esperada:** Relatório objetivo contendo: Match Score detalhado, pontos fortes do candidato frente à vaga, requisitos não atendidos e chamada para gerar kit adaptado.
- **Evidência:** Execução de `tool_explain_match`, mensagem outbox com chave `match_expl_...`.
- **Status:** PASS

### J-09: Perguntas Livres Sobre a Vaga
- **Input Humano:** "Quais tecnologias essa vaga pede?", "O que falta no meu perfil pra essa vaga?", "Essa empresa é remota?", "Qual o principal desafio dessa vaga?"
- **Intenção Esperada:** `JOB_INQUIRY_NATURAL`
- **Contexto Necessário:** Vaga ativa em contexto de sessão.
- **Ação:** Análise contextual dos requisitos e descrição da vaga contra o perfil da pessoa candidata, respondendo pontualmente à dúvida levantada.
- **Resposta Esperada:** Resposta direta e fundamentada estritamente nas informações da vaga e fatos reais do candidato, sem alucinação de dados.
- **Evidência:** Mensagem outbox explicativa contextualizada. Testes automatizados: `services/api/tests/messaging/test_pilot_flow.py::test_j09_active_job_match_inquiry_grounded_in_target_job` e `test_pilot_natural_language.py`.
- **Status:** PARCIAL / GATED (Análise e explicabilidade de compatibilidade da vaga ativa via `MATCH_EXPLANATION` com `tool_explain_match` / `tool_match_job` comprovada estruturalmente; Q&A livre descritivo sobre o texto da vaga como 'Essa empresa é remota?' retido sob portão de produto por demandar pipeline dedicado de LLM/Q&A e decisão formal de produto / ADR).

### J-10: Pedido Natural Para Candidatura
- **Input Humano:** "Quero me candidatar", "curti essa vaga, como faço?", "me ajuda a aplicar", "quero tentar essa oportunidade", "faz minha candidatura", "vamos tentar essa", "bora aplicar".
- **Intenção Esperada:** `APPLICATION_PREPARE_REQUEST`
- **Contexto Necessário:** Vaga ativa em contexto; candidato com perfil profissional cadastrado.
- **Ação:** Gatilho para alfaiataria de currículo (`tool_tailor_resume`), geração do Application Kit completo e criação do registro de candidatura (`Application`) no estado `PREPARING`.
- **Resposta Esperada:** Application Kit adaptado (currículo direcionado, carta de apresentação, destaques alinhados aos requisitos) entregue em partes estruturadas no WhatsApp.
- **Evidência:** `Application` criada em `applications`, draft persistido, mensagens outbox com sufixos `_part1`, `_part2`.
- **Status:** PASS

### J-11: Geração do Application Kit
- **Input Humano:** "Gera meu kit de candidatura", "preparar kit", "monta minha apresentação para a vaga".
- **Intenção Esperada:** `APPLICATION_KIT_GENERATION`
- **Contexto Necessário:** Vaga identificada e perfil com fatos confirmados.
- **Ação:** Construção do kit de candidatura fundamentado exclusivamente no Professional Truth Store (sem invenção de qualificações), contendo pitch, carta adaptada e respostas a perguntas de triagem.
- **Resposta Esperada:** Conteúdo do kit estruturado e legível, com orientação prática de submissão.
- **Evidência:** Chaves de outbox `kit_...`, ausência comprovada de fatos fabricados.
- **Status:** PASS

### J-12: Candidatura Marcada Como Realizada
- **Input Humano:** "Já me candidatei", "acabei de aplicar", "enviei o currículo lá", "candidatura enviada", "já mandei a aplicação".
- **Intenção Esperada:** `APPLICATION_STATUS_APPLIED`
- **Contexto Necessário:** Candidatura existente no estado `PREPARING`.
- **Ação:** Transição de estado de `Application.status` de `PREPARING` para `APPLIED`, registrando timestamp do envio.
- **Resposta Esperada:** Parabéns pela submissão, confirmação do status atualizado para APPLIED e oferta proativa de agendamento de lembrete de follow-up.
- **Evidência:** `Application.status == 'applied'`, mensagem outbox com chave `status_upd_...`.
- **Status:** PASS

### J-13: Acompanhamento e Status
- **Input Humano:** "Como estão minhas vagas?", "qual o status da minha candidatura?", "minhas candidaturas", "ver minhas vagas aplicadas".
- **Intenção Esperada:** `APPLICATION_STATUS_QUERY`
- **Contexto Necessário:** Usuário com histórico de candidaturas.
- **Ação:** Consulta às candidaturas recentes do usuário com detalhamento de status (preparando, enviada, em entrevista, finalizada).
- **Resposta Esperada:** Resumo das candidaturas ativas com nome da empresa, vaga, data e status atual.
- **Evidência:** Registros lidos da tabela `applications`, resposta formatada.
- **Status:** PASS

### J-14: Lembrete e Follow-up
- **Input Humano:** "Me lembra de acompanhar", "agenda um lembrete pra daqui a 3 dias", "quando devo falar com eles?", "lembrete de follow-up".
- **Intenção Esperada:** `REMINDER_SCHEDULE_REQUEST`
- **Contexto Necessário:** Candidatura ativa registrada.
- **Ação:** Criação de lembrete (`tool_schedule_follow_up`) agendado para data futura (padrão: 3 dias) associado à candidatura.
- **Resposta Esperada:** Confirmação do agendamento do lembrete com identificador e garantia de que o assistente entrará em contato via WhatsApp no momento oportuno.
- **Evidência:** Registro em `reminders` com `remind_at` futuro e `status = 'pending'`.
- **Status:** PASS

### J-15: Privacidade e LGPD
- **Input Humano:** "Privacidade", "como vocês tratam meus dados?", "quais informações vocês guardam?", "termos de uso", "lgpd".
- **Intenção Esperada:** `PRIVACY_INQUIRY`
- **Contexto Necessário:** Qualquer estado da conversa.
- **Ação:** Disparo de orientação transparente sobre categorias de dados coletados, finalidade restrita de carreira, período de retenção e direitos do titular sob a LGPD.
- **Resposta Esperada:** Resposta clara, amigável e informativa sobre proteção de dados, instruindo como solicitar exclusão.
- **Evidência:** Outbox com chave `privacy_...`, sem efeitos colaterais de alteração de dados.
- **Status:** PASS

### J-16: Exclusão de Dados
- **Input Humano:** "Quero apagar meus dados", "excluir meus dados", "deletar minha conta", "quero cancelar meu cadastro", "apagar tudo".
- **Intenção Esperada:** `DELETION_REQUEST`
- **Contexto Necessário:** Qualquer estado da conversa.
- **Ação:** Roteamento seguro que não executa exclusões destrutivas impulsivas via chat e instrui o procedimento canônico do piloto com o operador.
- **Resposta Esperada:** Informação formal sobre o procedimento de exclusão do piloto, garantia de respeito aos direitos e direcionamento para o canal do operador/suporte.
- **Evidência:** Nenhuma exclusão fantasma ou colisão com confirmação de fatos (Finding 1 da PR #19 protegido).
- **Status:** PASS

### J-17: Ajuda e Menu
- **Input Humano:** "Ajuda", "o que você faz?", "como você pode me ajudar?", "socorro", "menu", "o que posso fazer aqui?"
- **Intenção Esperada:** `HELP_ASSISTANCE_REQUEST`
- **Contexto Necessário:** Qualquer momento da interação.
- **Ação:** Fornecimento de menu de apoio orientando as capacidades principais do assistente de acordo com a fase em que o usuário se encontra.
- **Resposta Esperada:** Guia sucinto em linguagem natural explicando como enviar currículo, vagas, pedir análise, gerar kits e gerenciar candidaturas.
- **Evidência:** Outbox gerada com opções claras e tom acolhedor.
- **Status:** PASS

### J-18: Retomada de Conversa (Após Horas ou Dias)
- **Input Humano:** "Oi de novo", "voltei", "bom dia", "olá TapJob".
- **Intenção Esperada:** `CONVERSATION_RESUME`
- **Contexto Necessário:** Usuário já cadastrado com perfil existente e histórico prévio.
- **Ação:** Reconhecimento do candidato pelo vínculo de identidade (`channel_identity_links`), mantendo a integridade do estado e contextualizando o próximo passo produtivo.
- **Resposta Esperada:** Boas-vindas de retorno personalizadas, informando a prontidão do perfil e sugerindo retomar a análise de vagas ou candidaturas ativas.
- **Evidência:** Não reinicia o onboarding de primeiro contato nem duplica identidade.
- **Status:** PASS

### J-19: Mensagens Repetidas
- **Input Humano:** Envio duplo idêntico da mesma mensagem em curto intervalo (ex: rede lenta ou toque duplo).
- **Intenção Esperada:** `DUPLICATE_MESSAGE_HANDLING`
- **Contexto Necessário:** Webhook retransmitido ou envio do mesmo texto.
- **Ação:** Idempotência na camada de envelope (`uq_provider_event_envelopes_dedup`) ou geração de resposta idempotente sem efeitos colaterais duplicados.
- **Resposta Esperada:** Retorno HTTP 202 sem duplicar linhas no banco de dados e sem disparo de mensagens duplicadas para o candidato.
- **Evidência:** Único efeito persistido no banco de dados.
- **Status:** PASS

### J-20: Mensagens Ambíguas ou Incompletas
- **Input Humano:** "Sim", "pode ser", "ok", "legal", "entendi", "hmmm", "acho que sim".
- **Intenção Esperada:** `AMBIGUOUS_OR_AFFIRMATIVE_FOLLOWUP`
- **Contexto Necessário:** Mensagem curta enviada após proposta de ação pelo bot (ex: "Deseja gerar o kit?").
- **Ação:** Resolução contextual: se houve uma sugestão de candidatura recente, interpretação afirmativa aciona a geração do kit; se não houver contexto claro, resposta educada pedindo esclarecimento com sugestões práticas.
- **Resposta Esperada:** Continuidade suave do fluxo pretendido ou pergunta clarificadora amigável.
- **Evidência:** Ausência de mensagens de erro cruas ou quebras de fluxo.
- **Status:** PASS

### J-21: Falha Temporária do Provedor / Broker
- **Input Humano:** Qualquer mensagem recebida durante indisponibilidade transitória do Redis, timeout do Evolution API ou latência de storage.
- **Intenção Esperada:** `TRANSIENT_FAILURE_RESILIENCE`
- **Contexto Necessário:** Exceção recuperável de rede ou broker (`httpx.TimeoutException`, `ConnectionError`, `TransientProcessingError`).
- **Ação:** O worker agenda retentativa com backoff exponencial via Celery; a mensagem não é descartada e o estado permanece íntegro.
- **Resposta Esperada:** Entrega assíncrona assim que o broker ou serviço se restabelece, sem perder o evento do usuário.
- **Evidência:** Envelope ou outbox com status `retryable` e `attempts > 1` até o sucesso.
- **Status:** PASS

### J-22: Replay e Idempotência
- **Input Humano:** Retransmissão de webhook com o mesmo `provider_message_id` após crash do processo worker.
- **Intenção Esperada:** `REPLAY_IDEMPOTENCY_SAFETY`
- **Contexto Necessário:** `inbound_domain_effects` gravado ou chave de idempotência de outbox já gerada.
- **Ação:** Detecção de efeito pré-existente (`get_inbound_effect`); reutilização do resultado persistido sem duplicar `ResumeImport`, `Application` ou disparos outbox.
- **Resposta Esperada:** A mesma resposta esperada do fluxo original, sem duplicação de mensagens no WhatsApp do usuário.
- **Evidência:**
  1. Suíte Primária de Integração em CI (`backend-integration` com PostgreSQL e Redis reais provisionados):
     - `test_ingress_e2e_canary.py::test_real_ingress_e2e_canary_vertical_slice`: retransmissão de webhook com mesmo ID comprova zero duplicação de `User`, zero duplicação de `ChannelIdentityLink`, e zero duplicação de mensagens na `outbound_message_queue`.
     - `test_ingress_e2e_canary.py::test_conversational_e2e_canary_vertical_slice`: replay de webhook de candidatura comprova zero duplicação de `ApplicationKit` e zero duplicação de `Application` no PostgreSQL.
     - `test_ingress_e2e_canary.py::test_first_contact_job_url_and_replays_vertical_slice`: replay de intake de vaga comprova zero duplicação de `Job`, zero duplicação de `MatchResult` e zero duplicação de outbox no PostgreSQL.
     - `test_ingress_e2e_canary.py::test_finalize_review_crash_between_domain_commit_and_effect_completion_postgres`: recuperação de crash e replay idempotente com `InboundEffect` e confirmação canônica.
  2. Suíte de Banco Isolado e Orquestração:
     - `test_pilot_flow.py::test_j22_real_database_inbound_effect_and_reminder_idempotency`: desduplicação por constraint de envelope (`uq_provider_event_envelopes_dedup`), idempotência de `InboundEffect` e idempotência de `Reminder` com persistência real em banco isolado.
     - `test_pilot_flow.py::test_j22_dispatch_orchestration_replay_idempotency_keys`: determinismo de chaves de idempotência na camada de orquestração.
- **Status:** PARCIAL (Ponta a ponta garantido no ambiente de CI com PostgreSQL/Redis provisionados; ambiente de teste local isolado valida contratos de chave, desduplicação de envelope, InboundEffect e Reminder).
