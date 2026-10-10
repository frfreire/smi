# APS - Alagamentos Urbanos (Sistemas Distribuídos + Mobile)

Aplicação distribuída para dispositivo móvel que gerencia informações sobre
alagamentos e inundações urbanas (relatos colaborativos + registros oficiais de ocorrências do CEMADEN).

## Componentes
| Pasta | Conteúdo | Estado de verificação |
|---|---|---|
| `backend/api` | API REST + WebSocket (FastAPI), JWT, idempotência, versão/409, votação, alertas, relatórios, CSV | **Testado** (36 testes no total) e executado com uvicorn |
| `backend/ingestao` | Carga do CSV de ocorrências do CEMADEN (`dados/cemaden/`, extraído do portal em 09/10/2026): validação, descarte de linhas inválidas, lotes idempotentes com retry/backoff | **Testado** com o arquivo real (1.977 de 1.993 linhas válidas; 16 com coordenada 0,0 descartadas). Não há consulta online ao portal: a fonte é o arquivo exportado |
| `dados/cemaden` | Arquivo original do CEMADEN + `LEIAME.md` | Instantâneo de 16/07/2025 a 11/07/2026 |
| `cliente_referencia` | Fila offline, retry/backoff, circuit breaker em Python | **Testado** |
| `mobile` | App Flutter (mapa, relato, alertas ao vivo, resumo, fila offline) | **Não compilado** (sem SDK Flutter no ambiente); revisar com `flutter analyze` |
| `backend/Dockerfile`, `docker-compose.yml` | Implantação | **Não executado** (sem daemon Docker no ambiente) |
| `docs/openapi.json` | Contrato da API gerado do código | Gerado |
| `relatorio/` | Relatório no template LaTeX oficial da APS (pdfLaTeX): `cd relatorio && latexmk -pdf main.tex`; trechos de código regenerados por `python3 relatorio/scripts/gerar_trechos.py` | PDF compilado, 46 págs, limites de páginas conferidos |

## Executar o backend (sem Docker)
```bash
pip install -r backend/requirements.txt
export JWT_SECRET='um-segredo-com-32-ou-mais-caracteres' ADMIN_EMAIL=mod@aps.local ADMIN_SENHA='troque-isto-1' \
       SERVICO_EMAIL=svc@aps.local SERVICO_SENHA='troque-isto-2' DB_PATH=./aps.db
uvicorn backend.api.main:app --port 8000      # documentação interativa em /docs
API_URL=http://localhost:8000 CSV_PATH=dados/cemaden/reindesc_tabela_ocorrencias.csv INTERVALO_S=3600 \
  python -m backend.ingestao.ingestor        # carrega os dados do CEMADEN (idempotente)
python -m pytest tests -q                      # testes
```
## Executar o app
```bash
cd mobile && flutter create . --platforms=android,ios   # gera as pastas nativas (uma vez)
flutter pub get && flutter run --dart-define=API_URL=http://10.0.2.2:8000
```
Android: adicionar permissões `INTERNET`, `ACCESS_FINE_LOCATION` e `ACCESS_NETWORK_STATE` no `AndroidManifest.xml`.

## Roteiro de demonstração
1. Subir o backend e abrir `/health`.
2. Login, criar relato e vê-lo no mapa/lista (outro aparelho ou emulador).
3. 3 relatos na mesma região geram alerta ao vivo (WebSocket).
4. Modo avião: criar relato (fica `pendente`), religar e sincronizar.
5. Derrubar o backend (`Ctrl+C`/`docker stop`): mensagens de erro e retry.
6. Resumo, CSV (moderador) e votação de relatos.
