# Proposta de trabalho - APS 2023/2 (Ciência da Computação, 7º/8º) - Sistemas Distribuídos

**Para aprovação do(a) coordenador(a) auxiliar.**
**Integrantes (3):** [Nome 1 / RA] - [Nome 2 / RA] - [Nome 3 / RA]

## Tema escolhido
Gerenciamento de informações ambientais urbanas: **monitoramento colaborativo de alagamentos e inundações**.

## Problema e público-alvo
Pontos de alagamento surgem em minutos e a informação sobre eles é dispersa e sem verificação. Público: moradores, motoristas e usuários de transporte público que precisam evitar áreas alagadas, e moderadores/defesa civil que acompanham os relatos.

## Funcionalidades
- **Mínimas:** cadastro/login; relato de alagamento com localização e gravidade; mapa e listagem com filtros; alertas em tempo real; uso sem internet com sincronização posterior.
- **Extras planejadas:** votação para confirmar/refutar relatos e moderação; **uso dos dados de ocorrências do CEMADEN** (extraídos do portal em 09/10/2026): carga validada e idempotente, camada oficial no mapa, consulta por proximidade e alerta oficial para ocorrência hidrológica recente; alerta colaborativo por região; resumo estatístico e regiões críticas; exportação CSV.
- **Tratamento de erros:** timeout, retentativas com backoff e jitter, circuit breaker, reenvio idempotente, conflito de versão, mensagens por campo, GPS negado.

## Por que é distribuído
Componentes em processos separados que se comunicam por rede: aplicativo móvel (cliente com banco local), API de serviços, serviço de ingestão dos dados do CEMADEN e canal WebSocket de alertas. Há falhas parciais (rede móvel, servidor, fonte externa) e dados replicados no aparelho (consistência eventual), tratadas por idempotência, controle otimista de versão e carga idempotente e com retentativas dos dados oficiais.

## Plataforma e tecnologias
Flutter/Dart (Android e iOS, demonstração em emulador/simulador); servidor Python/FastAPI com JWT e SQLite (PostgreSQL em produção); WebSocket; Docker; dados do CEMADEN (CSV); OpenStreetMap.

## Cronograma e divisão de tarefas
8 semanas: proposta e arquitetura (1-2), MVP da API e do app (3-4), robustez e alertas (5), extras e testes de falha (6), revisão, Docker e ensaio (7), relatório e entrega (8).
Aluno 1: API, banco e segurança. Aluno 2: aplicativo e sincronização. Aluno 3: ingestão, testes, Docker e documentação. Relatório compartilhado.
