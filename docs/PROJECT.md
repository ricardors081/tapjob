# Tap Vaga — Project Charter

**Status:** Foundation / Product Definition  
**Mercado inicial:** Brasil  
**Expansão:** América Latina e global  
**Data-base desta versão:** 2026-09-29

## 1. Visão

Tap Vaga será um sistema operacional pessoal para a busca de emprego.

O usuário terá uma única identidade profissional que alimenta descoberta de vagas, matching, currículos, candidaturas, entrevistas, networking, acompanhamento e analytics. O mesmo histórico será acessível por app mobile, WhatsApp e, quando necessário, web/extensão de navegador.

O objetivo não é ser apenas um "portal de vagas" nem apenas um "gerador de currículo". O produto deve acompanhar o usuário do primeiro currículo até a contratação.

## 2. Problema

Hoje a pessoa candidata precisa alternar entre:
- LinkedIn;
- Indeed;
- Gupy;
- páginas de carreiras;
- ATSs como Greenhouse, Lever, Ashby e Workday;
- planilhas/notas;
- múltiplos currículos;
- e-mail;
- calendário;
- WhatsApp;
- ferramentas de IA.

Isso gera perda de vagas, currículos genéricos, candidaturas sem acompanhamento, dificuldade para saber o que funciona e baixa capacidade de preparação.

## 3. Proposta de valor

Tap Vaga concentra essa jornada em cinco capacidades:

1. **Encontrar:** descobrir e priorizar oportunidades.
2. **Preparar:** adaptar currículo, carta e respostas com verdade factual.
3. **Aplicar:** usar API oficial quando disponível; nos demais casos, reduzir o atrito com assistência.
4. **Acompanhar:** detectar e organizar a evolução de cada candidatura.
5. **Melhorar:** aprender com resultados, entrevistas e padrões de conversão.

## 4. Canais

### Mobile
Career Hub principal:
- dashboard;
- vagas;
- currículos;
- candidaturas;
- entrevistas;
- analytics;
- documentos;
- configurações.

### WhatsApp
Agente proativo:
- alertas;
- comandos rápidos;
- aprovação de ações;
- perguntas;
- preparação de candidatura;
- acompanhamento.

### Web
Experiência complementar:
- edição mais rica de currículo;
- dashboard;
- administração;
- operações que se beneficiem de desktop.

### Extensão
Apply Assistant:
- lê o formulário presente no navegador;
- sugere/mapeia respostas;
- preenche dados autorizados;
- anexa documento correto;
- para antes da submissão quando o modo exigir revisão humana.

## 5. Princípios de produto

- Mobile-first, mas não mobile-only.
- WhatsApp é canal; o Career Core é o produto.
- Uma fonte de verdade profissional.
- IA explicável e fundamentada.
- Usuário no controle de candidaturas e mensagens.
- Automação oficial antes de automação frágil.
- Portais externos são conectores substituíveis.
- Brasil primeiro.
- Privacidade e segurança desde a modelagem.
- Cada nova integração deve degradar com elegância para Assisted/Deep Link.

## 6. North Star

A métrica principal deve medir avanço real, não volume de automação.

Proposta inicial:
**Qualified Progress Rate (QPR): percentual de candidaturas relevantes que avançam para assessment, entrevista ou oferta.**

Métricas auxiliares:
- vagas relevantes encontradas por semana;
- taxa de abertura de alertas;
- application kit -> candidatura;
- candidatura -> resposta;
- candidatura -> entrevista;
- entrevista -> oferta;
- tempo até candidatura;
- tempo até follow-up;
- conversão por fonte, cargo, currículo e score;
- precisão do matching;
- falso positivo/negativo do inbox intelligence.

## 7. Público inicial

Pessoa candidata no Brasil que:
- procura emprego ativamente;
- usa LinkedIn/Gupy/Indeed e páginas de empresas;
- mantém currículo em PDF/DOCX;
- quer melhorar ATS e matching;
- precisa organizar múltiplas candidaturas;
- valoriza alertas e automação assistida.

## 8. Fora de escopo inicial

Não é objetivo de V1:
- ser ATS completo para recrutadores;
- vender banco de currículos para empresas;
- substituir os portais de vagas;
- burlar CAPTCHA, anti-bot ou restrições contratuais;
- enviar candidaturas indiscriminadamente;
- fabricar informações profissionais;
- operar recrutamento para empresas como produto principal.

## 9. Diferencial

A tese é unir:

- execução inspirada em AIApply;
- gestão inspirada em Huntr;
- descoberta/aplicação assistida inspirada em Simplify;
- agente conversacional estilo Appliable;
- tracking/inbox estilo Basafy/JobBot;
- Career Intelligence estilo career-ops;
- fundação SaaS/AI estilo JobSync.

## 10. Critério de sucesso do produto

O usuário deve conseguir dizer:

> "Meu agente conhece meu histórico, encontra oportunidades adequadas, prepara a melhor candidatura possível sem inventar nada, me ajuda a enviar e acompanha tudo até eu conseguir uma vaga."
