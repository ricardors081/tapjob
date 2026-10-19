# Guia de Teste de Aceitação Humana — WhatsApp Pilot (TapJob)

**Missão:** `WP-PILOT-2 — Practical Product Validation & Controlled Live Pilot`  
**Superfície:** WhatsApp (Instância Staging: `TapJob` / `+57 313 784-8445`)  
**Público:** Pessoas testadoras do piloto (Ricardo e convidados autorizados)  
**Filosofia de UX:** Conversa natural em Português do Brasil. **NÃO utilize frases engessadas ou mágicas**. Fale com o TapJob como falaria com um consultor de carreira humano no WhatsApp.

---

## 1. Princípios Essenciais

1. **Linguagem Livre e Natural:**  
   Você pode usar gírias profissionais, abreviações habituais (*"blz"*, *"show"*, *"bora"*, *"valeu"*), variações formais ou informais, e até pequenos erros de digitação comuns de celular. O motor de linguagem natural do TapJob reconhece a sua **intenção**, não apenas palavras-chave idênticas.

2. **Professional Truth Store (Invariante Inegociável):**  
   O TapJob **nunca inventa** experiências, skills ou fatos profissionais. Toda candidatura e kit gerado é estritamente fundamentado no que você declarou e confirmou.

3. **Segurança e Privacidade:**  
   Seus dados estão protegidos por design (LGPD). Operadores nunca visualizam dados sensíveis em texto claro nos relatórios de console.

---

## 2. Como Iniciar a Conversa

- **Número do Bot no WhatsApp:** `+57 313 784-8445`
- Abra a conversa no seu aplicativo oficial do WhatsApp (celular ou WhatsApp Web).
- Inicie a conversa espontaneamente.

---

## 3. Roteiro de Cenários de Teste

> [!TIP]
> Os exemplos abaixo são apenas **sugestões de inspiração**. Sinta-se 100% livre para escrever suas próprias frases com suas palavras reais!

---

### Cenário 1: Primeiro Contato e Apresentação
- **Objetivo:** Descobrir como o TapJob se apresenta e acolhe o candidato.
- **Como testar:**
  - *"Opa, beleza? Como funciona o TapJob?"*
  - *"Olá! O que você pode fazer por mim?"*
  - *"Boa tarde, gostaria de ajuda para conseguir um emprego novo."*
- **O que esperar:**
  - Resposta acolhedora e objetiva explicando o papel do assistente pessoal de carreira: intake de currículo, análise de vagas, preparação de application kits e tracking de candidaturas.

---

### Cenário 2: Envio de Perfil Profissional (Intake)
- **Objetivo:** Cadastrar suas competências e experiências.
- **Como testar:**
  - **Opção A (Texto Livre espontâneo):** Envie um parágrafo contando sua experiência real:  
    *"Sou Desenvolvedor Backend com 6 anos de experiência. Trabalho principalmente com Python, FastAPI, Docker e bancos de dados PostgreSQL na nuvem AWS."*
  - **Opção B (Documento):** Envie um arquivo PDF com seu currículo profissional.
- **O que esperar:**
  - O assistente processará o texto ou documento e apresentará a lista de **fatos identificados** numerados (habilidades, histórico profissional, formação) para sua validação.

---

### Cenário 3: Validação de Fatos (Truth Store)
- **Objetivo:** Aprovar, rejeitar ou corrigir itens identificados antes que entrem no seu perfil oficial.
- **Como testar:**
  - **Aprovação total:**  
    *"Tudo certo, pode salvar!"* ou *"Tá perfeito, salva aí"* ou *"Confirmar todos"*.
  - **Ajuste pontual:**  
    *"Muda o fato 1 para Arquiteto de Software"* ou *"Edita o 2 para Tech Lead"*.
  - **Rejeição pontual:**  
    *"Descarta o item 3"* ou *"Tira o fato 2 fora"*.
- **O que esperar:**
  - O assistente ajustará exatamente o fato mencionado e informará quantos fatos restam pendentes até a confirmação final. Quando concluído, seu perfil será promovido para a versão seguinte.

---

### Cenário 4: Envio de Vaga e Análise de Compatibilidade (Match)
- **Objetivo:** Avaliar a aderência do seu perfil a uma oportunidade de mercado.
- **Como testar:**
  - Envie o link de uma vaga real (LinkedIn, Gupy, Greenhouse, etc.) ou o texto descritivo da vaga:  
    *"Dá uma olhada nessa vaga: https://www.linkedin.com/jobs/view/123456789/ Desenvolvedor Python Pleno na TechCorp. Requisitos: Python, microsserviços, Docker e PostgreSQL."*
  - Em seguida, pergunte conversacionalmente:  
    *"O que você acha dessa vaga?"*  
    *"Qual meu match nela?"*  
    *"Será que tenho chance nessa oportunidade?"*  
    *"Vale a pena me candidatar?"*
- **O que esperar:**
  - Diagnóstico honesto de compatibilidade: pontos fortes atendidos pelo seu perfil comprovado e eventuais lacunas ou requisitos adicionais.

---

### Cenário 5: Candidatura e Application Kit Adaptado
- **Objetivo:** Gerar o kit de candidatura direcionado para a vaga analisada.
- **Como testar:**
  - Diga em linguagem natural:  
    *"Curti essa vaga, como faço?"*  
    *"Quero tentar essa oportunidade!"*  
    *"Me ajuda a aplicar pra essa vaga"*  
    *"Gera meu application kit"*
- **O que esperar:**
  - O assistente gerará o Application Kit adaptado (currículo direcionado e carta de apresentação fundamentados unicamente nos seus fatos cadastrados). A candidatura é registrada no estado `PREPARING` com as orientações do modo de aplicação (ex: `DEEP_LINK`).

---

### Cenário 6: Atualização de Status da Candidatura
- **Objetivo:** Informar que a candidatura externa foi submetida.
- **Como testar:**
  - *"Já me candidatei pelo site da empresa!"*  
  - *"Enviei minha aplicação lá"*  
  - *"Já enviei o currículo"*
- **O que esperar:**
  - O assistente confirma a mudança de status da vaga para `APPLIED` (*Candidatura Submetida*) e sugere os próximos passos (preparação para entrevista e lembrete de follow-up).

---

### Cenário 7: Acompanhamento e Lembrete de Follow-up
- **Objetivo:** Agendar um lembrete para não perder prazos do processo seletivo.
- **Como testar:**
  - *"Me lembra de acompanhar essa candidatura na semana que vem"*  
  - *"Quero agendar um lembrete de follow-up"*  
  - *"Qual o status da minha candidatura?"*
- **O que esperar:**
  - O assistente confirma o agendamento do lembrete associado à vaga real e exibe o status atualizado do processo.

---

### Cenário 8: Privacidade e LGPD
- **Objetivo:** Consultar a política de privacidade e os dados armazenados.
- **Como testar:**
  - *"Como vocês tratam a privacidade dos meus dados?"*  
  - *"O que vocês sabem sobre mim?"*  
  - *"Política de privacidade e LGPD"*
- **O que esperar:**
  - Resumo transparente sobre armazenamento exclusivo dos dados cadastrados, consentimento do candidato e canais para revisão ou exportação de dados.

---

## 4. Inspeção em Tempo Real para o Operador

Durante ou após a realização dos testes, o operador pode consultar o estado interno da aplicação via terminal local sem expor dados pessoais:

```bash
# 1. Visão geral da saúde dos canais e filas de envio:
uv run python tools/ops/pilot_inspect.py --summary

# 2. Inspecionar o candidato em teste (use seu telefone com DDD):
uv run python tools/ops/pilot_inspect.py --phone 5511999991234

# 3. Ver as últimas mensagens entregues na fila de saída:
uv run python tools/ops/pilot_inspect.py --recent-outbox 5
```

---

## 5. Como Reportar Comportamentos Inesperados

Se você observar qualquer uma das seguintes situações:
1. O assistente não compreendeu uma intenção óbvia;
2. Houve repetição desnecessária de mensagem ou loop;
3. O assistente demorou mais de 10 segundos para responder;
4. Qualquer dado incorreto ou inventado foi sugerido;

Anote a frase exata enviada e o horário aproximado para que possamos investigar os envelopes de webhook e aprimorar o classificador de linguagem natural.
