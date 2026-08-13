# Boletines RAG — CCPM

Sistema de búsqueda y consulta en lenguaje natural sobre los Boletines Oficiales de
la Provincia de Misiones. Combina RAG (Retrieval-Augmented Generation) con Ollama
para preguntas en lenguaje natural, y full-text search directo en Postgres para
búsquedas exactas sin pasar por el modelo de lenguaje. Todo corre 100% local
(sin servicios de IA en la nube).

## Arquitectura

```
Cliente externo (Postman, otro sistema)
        │
        │  POST /api/consultar        (pregunta en lenguaje natural + Ollama)
        │  POST /api/consultarbd      (búsqueda exacta directa en BD, sin Ollama)
        │  Authorization: Bearer <token>
        ▼
┌─────────────────────────────┐
│  Spring Boot (Java 25)       │  puerto 6000 — systemd: boletines-gateway
│  - Valida el token           │
│  - Traduce JSON (inglés      │
│    externo ⇄ español interno)│
└──────┬──────────────┬────────┘
       │              │
       │ (sin token,  │ (sin token,
       │  solo        │  solo
       │  127.0.0.1)  │  127.0.0.1)
       ▼              ▼
┌─────────────┐  ┌──────────────────┐
│ api_rag.py   │  │ api_rag_bd.py     │
│ puerto 8000  │  │ puerto 8081        │
│ systemd:     │  │ systemd:            │
│ boletines-   │  │ boletines-api-db    │
│ api-rag      │  │                     │
│              │  │ Full-text search    │
│ Búsqueda +   │  │ directo (phraseto_  │
│ Ollama       │  │ tsquery, orden      │
│ (qwen2.5:7b) │  │ exacto de frase)    │
└──────┬───────┘  └─────────┬───────────┘
       │                    │
       ▼                    ▼
┌─────────────────────────────────────┐
│  Postgres + pgvector (Docker)         │
│  tabla chunks, tabla boletines        │
│  (+ columnas texto_json, fts_vector)  │
└───────────────────────────────────────┘
```

También existe `chat.py`, la versión de consola del mismo motor de búsqueda de
`api_rag.py` (útil para debug rápido sin pasar por HTTP).

Los tres servicios (`api_rag.py`, `api_rag_bd.py`, gateway Java) corren como
servicios **systemd**: arrancan solos al bootear el servidor y se reinician
automáticamente si se caen.

**Acceso externo**: confirmado funcionando tanto por red interna de CCPM
(`10.10.0.126:6000`) como por IP pública (`138.117.77.149:6000`, con
port-forward gestionado por el área de redes de CCPM).

## Requisitos

- Ubuntu 22.04+ (o similar)
- Docker + Docker Compose
- Python 3.10+
- Tesseract OCR con el paquete de idioma español (`tesseract-ocr-spa`)
- [Ollama](https://ollama.com) instalado, con el modelo `qwen2.5:7b` descargado (`ollama pull qwen2.5:7b`)
- Java 25 (via [SDKMAN](https://sdkman.io)) + Maven

## Estructura del proyecto

```
.
├── docker-compose.yml           # Postgres + pgvector
├── preprocesar.py               # Extrae texto de los PDFs y lo carga en la tabla `chunks`
├── generar_embeddings.py        # Calcula embeddings de los chunks pendientes
├── generar_json_boletines.py    # Arma el texto completo por boletín (reconstruido desde
│                                 # `chunks`, sin duplicados de solapamiento) y lo guarda
│                                 # en `boletines.texto_json`
├── chat.py                      # Cliente de consola para probar el buscador RAG
├── api_rag.py                   # Servicio HTTP: búsqueda + Ollama (puerto 8000)
├── api_rag_bd.py                # Servicio HTTP: búsqueda exacta directa en BD (puerto 8081)
├── verificar_extraccion.py      # Auditoría: compara páginas/caracteres extraídos vs. el PDF real
├── boletinDB                    # Dump de la tabla `boletines` original (metadata de CCPM)
├── boletines/                   # Carpeta con los PDFs a procesar (no versionada, ver .gitignore)
├── boletines-rag-gateway/       # Proyecto Spring Boot (gateway REST con token de seguridad)
├── boletines-api-rag.service    # Unidad systemd de api_rag.py
├── boletines-api-db.service     # Unidad systemd de api_rag_bd.py
└── boletines-gateway.service    # Unidad systemd del gateway Java
```

## 1. Levantar la base de datos

```bash
docker compose up -d
```

Contenedor `boletin_db` (Postgres 17 + pgvector), puerto `5433`, volumen
persistente (`boletin_db_data`), `restart: unless-stopped`.

### Restaurar la tabla `boletines` (metadata original de CCPM)

```bash
docker cp boletinDB boletin_db:/tmp/boletinDB
docker exec -it boletin_db pg_restore -U postgres -d boletinDB /tmp/boletinDB
```

### Crear la tabla `chunks`

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE public.chunks (
    id SERIAL PRIMARY KEY,
    boletin_id INT REFERENCES public.boletines(id),
    nro_boletin BIGINT,
    archivo VARCHAR(255),
    pagina INT,
    pagina_fin INT,
    fragmento_nro INT,
    tipo_acto VARCHAR(50),
    numero_acto VARCHAR(50),
    entidades JSONB DEFAULT '{}',
    tipo_extraccion VARCHAR(10),
    texto TEXT NOT NULL,
    texto_busqueda tsvector GENERATED ALWAYS AS (to_tsvector('spanish', texto)) STORED,
    embedding VECTOR(384),
    creado_en TIMESTAMP DEFAULT now()
);

CREATE INDEX idx_chunks_texto_busqueda ON public.chunks USING GIN (texto_busqueda);
CREATE INDEX idx_chunks_embedding ON public.chunks USING hnsw (embedding vector_cosine_ops);
CREATE UNIQUE INDEX idx_chunks_dedupe ON public.chunks (nro_boletin, pagina, fragmento_nro);
```

### Extender la tabla `boletines` (texto completo + full-text search)

```sql
-- Texto completo del boletín, reconstruido desde `chunks` por generar_json_boletines.py
ALTER TABLE public.boletines ADD COLUMN IF NOT EXISTS texto_json JSONB;

-- Índice full-text sobre el texto completo, para /api/consultarbd
ALTER TABLE public.boletines ADD COLUMN IF NOT EXISTS fts_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('spanish', texto_json->>'texto_completo')) STORED;
CREATE INDEX IF NOT EXISTS idx_boletines_fts ON public.boletines USING GIN (fts_vector);

-- Opcional: búsqueda por substring (ILIKE) rápida, si hace falta además del full-text
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_boletines_texto_trgm
    ON public.boletines USING gin ((texto_json->>'texto_completo') gin_trgm_ops);
```

## 2. Preparar el entorno Python

```bash
sudo apt install tesseract-ocr tesseract-ocr-spa
pip3 install fitz pymupdf opencv-python-headless pytesseract psycopg2-binary \
             sentence-transformers fastapi uvicorn sqlalchemy
```

Poné los PDFs a procesar en `./boletines/`.

## 3. Procesar los PDFs → tabla `chunks`

```bash
python3 preprocesar.py
```

- Extrae texto de cada página: nativo, OCR (Tesseract, DPI 500), o ambos combinados
  si la página tiene texto tipeado *y* imágenes (sellos, firmas, anexos escaneados).
- Chunking simple: fragmentos de ~1000 caracteres con 200 de solapamiento (sin
  dividir por tipo de acto -- se probó y se descartó, ver notas de diseño).
- Vincula cada PDF a su `nro_boletin` por coincidencia de nombre de archivo
  contra `patharchivo` en `boletines`.
- Incremental, paralelo (`NUM_WORKERS` = núcleos físicos, no lógicos),
  `OMP_THREAD_LIMIT=1` para que Tesseract no compita consigo mismo dentro de
  cada proceso worker.
- Progreso/ETA en vivo; errores en `errores_preprocesamiento.log`.

```bash
tmux new -s preprocesar
python3 preprocesar.py
# Ctrl+B, D para dejarlo corriendo en background
```

## 4. Generar embeddings

```bash
python3 generar_embeddings.py
```

Vectoriza en lotes los chunks con `embedding IS NULL`, con
`paraphrase-multilingual-MiniLM-L12-v2`. Incremental.

## 5. Generar el texto completo por boletín

```bash
python3 generar_json_boletines.py            # solo los boletines nuevos
python3 generar_json_boletines.py --forzar   # reprocesa todos (por ejemplo, tras
                                               # cambiar algo en `chunks`)
```

Reconstruye, por cada boletín, el texto completo agrupando sus chunks por página
y **sacando el solapamiento de 200 caracteres repetido** entre fragmentos
consecutivos, y lo guarda en `boletines.texto_json`:

```json
{
  "boletin_numero": 10529,
  "archivo": "bo10529.pdf",
  "total_paginas": 22,
  "paginas": [{"pagina": 1, "tipo_extraccion": "nativo", "texto": "..."}],
  "texto_completo": "todo el boletín junto"
}
```

No relee los PDFs ni corre OCR -- solo reorganiza lo que `preprocesar.py` ya
extrajo. Rápido (minutos, no horas).

## 6. Verificar la extracción (opcional pero recomendado)

```bash
python3 verificar_extraccion.py
```

Compara páginas/caracteres reales del PDF contra lo guardado en `chunks`, para
detectar extracciones incompletas o de mala calidad.

## 7. Probar el buscador RAG por consola

```bash
python3 chat.py
```

Router de 3 niveles: full-text estricto (`phraseto_tsquery`, exige orden exacto
de frase) → full-text flexible (`OR` entre palabras, red de seguridad si el
estricto no encuentra nada) → semántico (pgvector). **Si el Nivel 1 (estricto)
encuentra algún resultado, corta ahí** y no completa con los otros niveles.

Antes de buscar, se sacan palabras de relleno conversacional ("boletín",
"hablen", "menciona", "cuantos", etc.) que no aportan y diluyen el ranking.

## 8. Servicios en producción (systemd)

```bash
# Compilar el jar de Java
cd boletines-rag-gateway
mvn package -DskipTests
cd ..

# Instalar las unidades (ajustar el token real en boletines-gateway.service antes)
sudo cp boletines-api-rag.service boletines-api-db.service boletines-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable boletines-api-rag boletines-api-db boletines-gateway
sudo systemctl start boletines-api-rag boletines-api-db boletines-gateway
```

Uso diario:
```bash
sudo systemctl status boletines-api-rag boletines-api-db boletines-gateway
sudo systemctl restart boletines-gateway   # tras recompilar el jar
tail -f /var/log/boletines-gateway.log
tail -f /var/log/boletines-api-rag.log
tail -f /var/log/boletines-api-db.log
```

**Importante**: `api_rag.py` (8000) y `api_rag_bd.py` (8081) deben correr con
`--host 127.0.0.1` (no `0.0.0.0`) -- no tienen autenticación propia, confían en
que solo Java les habla desde la misma máquina. Toda la seguridad hacia el
exterior la maneja el gateway Java.

## 9. Endpoints

### `POST /api/consultar` -- pregunta en lenguaje natural (usa Ollama)

```bash
curl -X POST http://127.0.0.1:6000/api/consultar \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"question": "decreto 512"}'
```

Request:
```json
{ "question": "decreto 512", "history": [{"question": "...", "answer": "..."}] }
```
(`history` opcional, para preguntas de seguimiento)

Response:
```json
{
  "answer": "...",
  "sources": [{"bulletinNumber": 14419, "file": "14419.pdf", "page": 3, "pageEnd": 3}]
}
```

### `POST /api/consultarbd` -- búsqueda exacta directa en BD (sin Ollama, rápido)

```bash
curl -X POST http://127.0.0.1:6000/api/consultarbd \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "GONZALEZ FACUNDO"}'
```

Request:
```json
{ "query": "GONZALEZ FACUNDO", "limit": 50 }
```
Exige que las palabras aparezcan **juntas y en ese orden exacto** (`phraseto_tsquery`)
-- distinto del comportamiento flexible de `/api/consultar`.

Response:
```json
{
  "rowCount": 2,
  "results": [
    {"bulletinNumber": 14268, "file": "14268pdf0.pdf", "date": "2016-06-...", "description": "..."}
  ]
}
```

Sin el header `Authorization` correcto, ambos endpoints responden `401`.

## Variables de configuración importantes

| Archivo | Variable | Qué controla |
|---|---|---|
| `preprocesar.py` | `NUM_WORKERS` | Procesos en paralelo para OCR. Núcleos físicos, no lógicos. |
| `preprocesar.py` | DPI del OCR | 500 -- necesario para leer bien dígitos en escaneos viejos. |
| `api_rag.py` | `MODELO_OLLAMA` | Actualmente `qwen2.5:7b`. Modelos más chicos (`3b`) responden más rápido pero con más riesgo de respuestas inconsistentes/contradictorias. |
| `api_rag.py` | `OLLAMA_OPTIONS` | `num_ctx=16384`, `num_predict=1000`, `num_thread=15`, `num_batch=512`. |
| `api_rag_bd.py` | `DATABASE_URL` | Conexión a Postgres (env var, con default). |
| `boletines-rag-gateway/application.properties` | `server.port` | **6000** (no 8080 -- cambiado por disponibilidad de port-forward externo). |
| `boletines-rag-gateway/application.properties` | `boletines.api.token` | Token esperado por el gateway. |
| `boletines-rag-gateway/application.properties` | `boletines.rag.base-url` / `boletines.db.base-url` | URLs de los dos servicios Python internos (8000 / 8081). |

## Pendientes conocidos

- **HTTPS**: pendiente hasta contar con un dominio para emitir certificado. Alternativa
  ya probada para pruebas puntuales: Cloudflare Tunnel (`cloudflared tunnel --url
  http://localhost:6000`) da HTTPS automático con una URL temporal, sin depender
  del área de redes -- útil para pruebas rápidas, no para uso permanente (la URL
  cambia en cada reinicio del túnel).
- **Detección de tipo de acto** (decreto/resolución/ley + número) se probó y se
  descartó a favor de un chunking simple y uniforme.

## Notas de diseño relevantes

- El texto completo de cada fragmento se guarda siempre entero -- las columnas
  `tipo_acto`/`numero_acto`/`entidades` en `chunks` son metadata opcional sin usar hoy.
- `nro_boletin` desnormalizado en `chunks` (además del FK `boletin_id`) a propósito,
  para evitar el JOIN en el camino caliente de cada búsqueda.
- `api_rag.py` y `api_rag_bd.py` solo escuchan en `127.0.0.1` -- nunca expuestos
  directamente; toda la seguridad la maneja el gateway Java.
- El token se compara con `MessageDigest.isEqual` (tiempo constante), no
  `String.equals`, para no filtrarlo por análisis de timing.
- `/api/consultar` y `/api/consultarbd` usan criterios de matching **distintos a
  propósito**: el primero prioriza precisión con fallback flexible (para preguntas
  conversacionales), el segundo exige frase exacta (para búsquedas puntuales rápidas).
