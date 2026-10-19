# WhatsApp Pilot Operational Runbook (Controlled Beta)

Este runbook define os procedimentos operacionais, checklists pré-voo, comandos de monitoramento e mitigação de falhas para o piloto controlado do TapJob via WhatsApp.

---

## 1. Objetivos e Escopo do Piloto

- **Público-alvo:** 5 a 10 participantes controlados (equipe interna e canary testers autorizados).
- **Escopo funcional validado:**
  1. Primeiro contato amigável com transparência ativa de privacidade e direitos do titular.
  2. Envio de currículo (PDF/documento) ou colagem de texto profissional.
  3. Extração e confirmação conversacional de fatos (Professional Truth Store).
  4. Matching inteligente com vagas reais via Career Core.
  5. Geração e entrega do **Application Kit adaptado diretamente em texto na conversa** ("quero me candidatar").
  6. Exercício de direitos do titular via comandos conversacionais (`privacidade`, `excluir meus dados`).
- **Invariante de Segurança:** Proibido envio de mensagens para contatos fora do escopo canary aprovado.

---

## 2. Checklist Pré-Piloto (Go / No-Go)

Antes de iniciar a sessão com qualquer participante:

| Item | Verificação | Critério de Sucesso |
|---|---|---|
| **1. Banco de Dados** | `alembic upgrade head` | Migrações aplicadas; conexões estáveis |
| **2. Celery Worker** | `celery -A tapjob_api.celery_app inspect ping` | Worker ativo escutando filas de mensageria |
| **3. Celery Beat** | Verificação de logs do scheduler | Tarefas periódicas agendadas (outbox sweep 5s, sweep órfãos 60s) |
| **4. Evolution API** | `GET /instance/connectionState/<instance>` | Estado `open`, QR code autenticado |
| **5. Webhook Ingress** | Verificação de secret HMAC | Webhook autenticado em `/api/v1/messaging/webhook/evolution` |
| **6. Canary Scope** | Tabela ou config de números permitidos | Somente números do piloto processados |

---

## 3. Monitoramento em Tempo Real (Pilot Inspect CLI)

A ferramenta operacional oficial é `tools/ops/pilot_inspect.py`. Todas as saídas mascaram PII automaticamente (números de telefone mascarados como `55119****1234`, nomes `R*** S***`, emails `c***o@dominio.com`).

### 3.1 Resumo de Saúde Geral do Piloto
```bash
uv run python tools/ops/pilot_inspect.py --summary
```
Retorna veredito (`HEALTHY` ou `DEGRADED`), total de participantes únicos, envelopes recebidos por status e fila outbox.

### 3.2 Últimos Envelopes Inbound Recebidos
```bash
uv run python tools/ops/pilot_inspect.py --recent-envelopes 20
```
Exibe tabela com ID, timestamp UTC, remetente mascarado, status (`processed`, `processing`, `failed`), tentativas e sumário de erros.

### 3.3 Mensagens Recentes na Fila Outbox
```bash
uv run python tools/ops/pilot_inspect.py --recent-outbox 20
```
Exibe mensagens enviadas e pendentes com destinatário mascarado, status (`sent`, `pending`, `retryable`, `dead`), tentativas e ID do provedor WhatsApp.

### 3.4 Histórico Específico de um Participante
```bash
uv run python tools/ops/pilot_inspect.py --phone 5511999991234
```
Exibe linha do tempo completa de mensagens recebidas e respostas enviadas para o participante.

---

## 4. Modos de Falha e Procedimentos de Mitigação

### 4.1 Queda do Broker Redis / Celery Temporário
- **Sintoma:** O trigger imediato de outbox falha com warning no log.
- **Comportamento do sistema:** Transações de banco de dados continuam bem-sucedidas. A mensagem permanece gravada na tabela `outbound_message_queue` com status `pending`.
- **Mitigação:** Assim que o worker/broker se restabelece, o Celery Beat executa `messaging.dispatch_outbox` e entrega as mensagens pendentes com bounded exponential backoff.
- **Ação do operador:** Reiniciar container Redis se necessário. Nenhuma mensagem é perdida.

### 4.2 Timeout ou Lentidão do Provedor de LLM
- **Sintoma:** `SoftTimeLimitExceeded` registrado no log da tarefa Celery.
- **Comportamento do sistema:** O worker captura a exceção, marca o envelope com `failed (retryable=True)` e agenda backoff exponencial sem consumir o processo worker.
- **Ação do operador:** Verificar quota e latência da API de IA configurada. Se persistir, o usuário recebe resposta instruindo tentativa em alguns minutos.

### 4.3 Desconexão da Instância do WhatsApp (Evolution API)
- **Sintoma:** `claim_and_dispatch_outbox_batch` registra erro de porta de transporte (`PortError`).
- **Comportamento do sistema:** Mensagens outbox são marcadas como `retryable` e reagendadas com backoff exponencial. Mensagens NÃO são descartadas.
- **Mitigação:**
  1. Acessar painel do Evolution API.
  2. Verificar estado da instância e reconectar via QR Code se a sessão expirou.
  3. Com a instância reconectada, o próximo ciclo do outbox entrega automaticamente todas as mensagens represadas.

### 4.4 Webhook Duplicado ou Reenvio de Rede
- **Sintoma:** WhatsApp retransmite o mesmo evento devido a timeout de ACK upstream.
- **Comportamento do sistema:** A restrição `uq_provider_event_envelopes_dedup` detecta duplicidade pelo `external_event_id` e descarta a mensagem duplicada no ponto de entrada (202 Accepted sem reprocessamento).

---

## 5. Gestão de Privacidade e Solicitações LGPD

Durante o piloto, qualquer participante pode exercer seus direitos:
1. **Comando `privacidade`:** O sistema responde informando as categorias de dados tratadas, a finalidade estrita de carreira e as orientações para exercício de direitos, sem jargões ou conclusões jurídicas precipitadas.
2. **Comando `excluir meus dados`:** O sistema orienta o candidato de forma transparente que, por segurança da conta no piloto, a exclusão definitiva é processada via procedimento canônico com o operador do piloto, sem executar exclusões destrutivas automáticas por comandos de chat.
3. **Exclusão Operacional:** Quando solicitada formalmente pelo participante, o operador do piloto processa a eliminação via procedimento canônico autorizado com registro de auditoria.

---

## 6. Critérios de Saída do Piloto (Exit Criteria)

O piloto é considerado concluído com sucesso quando:
- Mínimo de 5 participantes concluírem o ciclo completo (boas-vindas -> currículo -> confirmação de fatos -> vaga -> kit de candidatura entregue em texto).
- 0 mensagens presas em status `dead` ou `dead_letter` sem causa raiz diagnosticada.
- Latência mediana fim-a-fim de resposta conversacional inferior a 5 segundos.
- 100% de conformidade com as garantias do Professional Truth Store (zero alucinações de fatos profissionais).
- Relatório de encerramento aprovado para liberação da próxima fase.
