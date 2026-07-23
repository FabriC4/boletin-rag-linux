# Boletines RAG — CCPM

Sistema de búsqueda y consulta en lenguaje natural sobre los Boletines Oficiales de
la Provincia de Misiones, usando RAG (Retrieval-Augmented Generation) 100% local:
Postgres + pgvector para almacenamiento y búsqueda, Ollama para la generación de
respuestas, y un gateway en Spring Boot para exponerlo como API REST con seguridad.

## Arquitectura

```
Cliente externo (Postman, otro sistema)
        │  POST /api/consultar
        │  Authorization: Bearer <token>
        ▼
┌─────────────────────────────┐
│  Spring Boot (Java 25)       │  puerto 8080
│  - Valida el token           │
│  - Traduce JSON (snake_case  │
│    ⇄ camelCase)              │
└──────────┬────────────────────┘
           │ POST /consultar (sin token — solo accesible desde localhost)
           ▼
┌─────────────────────────────┐
│  FastAPI (api_rag.py)        │  puerto 8000, solo 127.0.0.1
│  - Carga el modelo de        │
│    embeddings UNA vez        │
│  - Búsqueda híbrida en       │
│    Postgres                  │
│  - Llama a Ollama             │
└──────────┬────────────────────┘
           │
           ▼
┌─────────────────────────────┐      ┌──────────────────┐
│  Postgres + pgvector          │      │  Ollama            │
│  (Docker), tabla `chunks`     │      │  (qwen2.5)          │
└─────────────────────────────┘      └──────────────────┘
```

También existe `chat.py`, la versión de consola del mismo motor de búsqueda
(útil para debug y pruebas rápidas sin pasar por HTTP).

## Requisitos

- Ubuntu 22.04+ (o similar)
- Docker + Docker Compose
- Python 3.10+
- Tesseract OCR con el paquete de idioma español (`tesseract-ocr-spa`)
- [Ollama](https://ollama.com) instalado, con el modelo `qwen2.5` descargado (`ollama pull qwen2.5`)
- Java 25 (via [SDKMAN](https://sdkman.io)) + Maven — solo si vas a correr el gateway Spring Boot

## Estructura del proyecto

```
.
├── docker-compose.yml          # Postgres + pgvector
├── preprocesar.py              # Extrae texto de los PDFs y lo carga en la tabla `chunks`
├── generar_embeddings.py       # Calcula embeddings de los chunks pendientes
├── chat.py                     # Cliente de consola para probar el buscador
├── api_rag.py                  # Envuelve la misma lógica de chat.py como servicio HTTP (FastAPI)
├── verificar_extraccion.py     # Auditoría: compara páginas/caracteres extraídos vs. el PDF real
├── boletinDB                   # Dump de la tabla `boletines` (metadata original de CCPM)
├── boletines/                  # Carpeta con los PDFs a procesar (no versionada, ver .gitignore)
└── boletines-rag-gateway/      # Proyecto Spring Boot (gateway REST con token de seguridad)
```

## 1. Levantar la base de datos

```bash
docker compose up -d
```

Esto crea el contenedor `boletin_db` (Postgres 17 + pgvector) en el puerto `5433`,
con un volumen persistente (`boletin_db_data`) para que los datos sobrevivan a
reinicios del contenedor.

### Restaurar la tabla `boletines` (metadata original)

```bash
docker cp boletinDB boletin_db:/tmp/boletinDB
docker exec -it boletin_db pg_restore -U postgres -d boletinDB /tmp/boletinDB
```

### Crear la tabla `chunks`

```bash
docker exec -it boletin_db psql -U postgres -d boletinDB
```

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

## 2. Preparar el entorno Python

```bash
sudo apt install tesseract-ocr tesseract-ocr-spa
pip3 install fitz pymupdf opencv-python-headless pytesseract psycopg2-binary \
             sentence-transformers fastapi uvicorn
```

Poné los PDFs a procesar en `./boletines/`.

## 3. Procesar los PDFs

```bash
python3 preprocesar.py
```

Qué hace:
- Extrae texto de cada página: nativo, OCR (Tesseract, DPI 500), o ambos combinados
  si la página tiene texto tipeado *y* imágenes (sellos, firmas, anexos escaneados).
- Corta el texto en fragmentos de ~1000 caracteres con 200 de solapamiento.
- Cada PDF se vincula a su `nro_boletin` real buscando coincidencia de nombre de
  archivo contra la columna `patharchivo` de la tabla `boletines`.
- Es **incremental**: si ya cargaste un boletín, lo salta en la próxima corrida.
- Corre en paralelo (`multiprocessing`, `NUM_WORKERS` ajustado a núcleos físicos,
  no lógicos — importante para no sufrir contención por hyperthreading).
- Progreso y ETA en vivo cada 25 archivos; errores van a `errores_preprocesamiento.log`.

Para correrlo sin que se corte si se pierde la conexión SSH:
```bash
tmux new -s preprocesar
python3 preprocesar.py
# Ctrl+B, D para salir dejándolo corriendo
```

## 4. Generar embeddings

```bash
python3 generar_embeddings.py
```

Busca los chunks con `embedding IS NULL`, los vectoriza en lotes con
`paraphrase-multilingual-MiniLM-L12-v2`, y guarda el resultado. También
incremental — se puede cortar y retomar sin perder trabajo.

## 5. Verificar la extracción (opcional pero recomendado)

```bash
python3 verificar_extraccion.py
```

Compara, por cada PDF, cuántas páginas y caracteres tiene realmente contra lo
que quedó guardado en la tabla — para detectar extracciones incompletas o de
mala calidad antes de confiar en los resultados.

## 6. Probar el buscador por consola

```bash
python3 chat.py
```

El buscador combina 3 niveles, en orden:
1. **Full-text estricto** (Postgres `tsvector`/`plainto_tsquery`, español): todas las
   palabras clave deben aparecer juntas. Máxima precisión.
2. **Full-text flexible** (OR): si el estricto no encuentra nada, relaja a
   "cualquiera de las palabras clave" — necesario para preguntas conversacionales
   ("¿qué boletines hablan de...?").
3. **Semántico** (pgvector, similitud coseno): último recurso, para preguntas
   conceptuales sin coincidencia literal de palabras.

Antes de buscar, se sacan palabras de relleno conversacional ("boletín", "hablen",
"menciona", etc.) que no aportan y diluyen el ranking.

## 7. Levantar el servicio HTTP interno (api_rag.py)

```bash
tmux new -s api-rag
uvicorn api_rag:app --host 127.0.0.1 --port 8000 --workers 1
```

`--workers 1` es intencional: el modelo de embeddings y la conexión a Postgres
viven en memoria del proceso; con más workers se duplicarían innecesariamente.

Probar:
```bash
curl -X POST http://127.0.0.1:8000/consultar \
  -H "Content-Type: application/json" \
  -d '{"pregunta": "decreto 512"}'
```

## 8. Levantar el gateway Spring Boot

```bash
cd boletines-rag-gateway
export BOLETINES_API_TOKEN=elegí-un-token-largo-y-random
mvn spring-boot:run
```

Endpoint expuesto: `POST /api/consultar` en el puerto `8080`, protegido por
header `Authorization: Bearer <token>` (comparación en tiempo constante,
`MessageDigest.isEqual`, para no filtrar el token por timing).

Probar:
```bash
curl -X POST http://127.0.0.1:8080/api/consultar \
  -H "Authorization: Bearer elegí-un-token-largo-y-random" \
  -H "Content-Type: application/json" \
  -d '{"pregunta": "decreto 512"}'
```

Sin el header correcto, debe responder `401`.

## Variables de configuración importantes

| Archivo | Variable | Qué controla |
|---|---|---|
| `preprocesar.py` | `NUM_WORKERS` | Procesos en paralelo para OCR. Basado en núcleos **físicos**, no lógicos. |
| `preprocesar.py` | DPI del OCR (`get_pixmap(dpi=...)`) | 500 por defecto — necesario para leer bien dígitos en escaneos viejos/de baja calidad. |
| `chat.py` / `api_rag.py` | `TOP_K` | Cantidad de fragmentos que se le pasan a Ollama como contexto. |
| `chat.py` / `api_rag.py` | `OLLAMA_OPTIONS` | `num_ctx`, `num_predict`, `num_thread`, `temperature` — ajustar `num_thread` a núcleos físicos reales del servidor. |
| `boletines-rag-gateway/application.properties` | `boletines.api.token` | Token esperado (via env var `BOLETINES_API_TOKEN`). |
| `boletines-rag-gateway/application.properties` | `boletines.rag.base-url` | URL del servicio Python (default `http://127.0.0.1:8000`). |

## Pendientes conocidos (no resueltos todavía)

- **Exposición fuera de la red interna**: el puerto `8080` no está habilitado en el
  firewall/NAT perimetral de CCPM — pendiente de gestión con el área de redes.
- **Persistencia de los procesos**: hoy `api_rag.py` y el gateway Java dependen de
  sesiones `tmux` abiertas manualmente. Para producción conviene migrarlos a
  servicios `systemd` que arranquen solos y se reinicien ante caídas.
- **HTTPS**: pendiente hasta contar con un dominio para emitir certificado
  (Let's Encrypt no puede emitir certificados sobre una IP pelada).
- **Detección de tipo de acto (decreto/resolución/ley + número)** se probó en una
  iteración anterior pero se descartó a favor de un chunking simple y uniforme;
  el full-text + semántico compensan la falta de esa estructura.

## Notas de diseño relevantes

- El texto completo de cada fragmento se guarda siempre entero en la tabla — las
  columnas `tipo_acto`/`numero_acto`/`entidades` son metadata adicional opcional,
  nunca reemplazan ni recortan el texto original.
- `nro_boletin` está desnormalizado en `chunks` (además del `boletin_id` con FK) a
  propósito, para evitar el JOIN en el camino caliente de cada búsqueda — hay
  muchísimas más lecturas que escrituras en este sistema.
- El servicio Python (`api_rag.py`) solo escucha en `127.0.0.1` — nunca queda
  expuesto directamente; toda la seguridad hacia el exterior la maneja el gateway Java.
