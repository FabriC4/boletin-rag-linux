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
│  Spring Boot (Java 25)       │  puerto 8080 — systemd: boletines-gateway
│  - Valida el token           │
│  - Traduce JSON (inglés      │
│    externo ⇄ español interno)│
└──────────┬────────────────────┘
           │ POST /consultar (sin token — solo accesible desde localhost)
           ▼
┌─────────────────────────────┐
│  FastAPI (api_rag.py)        │  puerto 8000, solo 127.0.0.1 — systemd: boletines-api-rag
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

Los dos servicios (`api_rag.py` y el gateway Java) corren como servicios
**systemd** (`boletines-api-rag` y `boletines-gateway`): arrancan solos al
bootear el servidor y se reinician automáticamente si se caen. Ya no dependen
de sesiones `tmux` abiertas a mano.

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
├── boletines-rag-gateway/      # Proyecto Spring Boot (gateway REST con token de seguridad)
├── boletines-api-rag.service   # Unidad systemd del servicio Python
└── boletines-gateway.service   # Unidad systemd del gateway Java
```

## 1. Levantar la base de datos

```bash
docker compose up -d
```

Esto crea el contenedor `boletin_db` (Postgres 17 + pgvector) en el puerto `5433`,
con un volumen persistente (`boletin_db_data`) y `restart: unless-stopped` para
que sobreviva tanto a reinicios del contenedor como del servidor.

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

> `tipo_acto`, `numero_acto` y `entidades` quedan siempre vacíos con el pipeline
> actual (se probó detectarlos automáticamente y se descartó, ver "Pendientes").
> Por eso no tienen índice — indexar una columna siempre vacía es puro costo de
> escritura sin ningún beneficio de lectura.

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
(esto es solo para la carga inicial de PDFs, es un proceso puntual que termina —
no confundir con los servicios permanentes de abajo, que corren por `systemd`)

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

## 7. Servicios en producción (systemd)

`api_rag.py` (puerto 8000, interno) y el gateway Java (puerto 8080) corren como
servicios systemd, no manualmente. Instalación (una sola vez):

```bash
# Compilar el jar de Java
cd boletines-rag-gateway
mvn package -DskipTests
cd ..

# Instalar las unidades (ajustar el token real en boletines-gateway.service antes)
sudo cp boletines-api-rag.service boletines-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable boletines-api-rag boletines-gateway
sudo systemctl start boletines-api-rag boletines-gateway
```

Uso diario:
```bash
sudo systemctl status boletines-api-rag boletines-gateway   # ver si están vivos
sudo systemctl restart boletines-gateway                    # tras recompilar el jar
tail -f /var/log/boletines-gateway.log                      # logs del gateway
tail -f /var/log/boletines-api-rag.log                      # logs del servicio Python
```

Si modificás el código Java: recompilar (`mvn package -DskipTests`) y
`sudo systemctl restart boletines-gateway` — no hace falta reinstalar la unidad.

## 8. Probar el endpoint completo

```bash
curl -X POST http://127.0.0.1:8080/api/consultar \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"question": "decreto 512"}'
```

Sin el header correcto, debe responder `401`.

### Contrato de la API (en inglés)

**Request:**
```json
{
  "question": "decreto 512",
  "history": [{"question": "...", "answer": "..."}]
}
```
(`history` es opcional, para preguntas de seguimiento)

**Response:**
```json
{
  "answer": "...",
  "sources": [
    {"bulletinNumber": 14419, "file": "14419.pdf", "page": 3, "pageEnd": 3}
  ]
}
```

Internamente, Java le sigue hablando a `api_rag.py` en español
(`pregunta`/`respuesta`/`fuentes`) — la traducción entre los dos idiomas vive
en `RagQueryService.java`, ni el cliente externo ni Python se enteran del otro lado.

### Acceso desde otras máquinas

- **Red interna de CCPM**: accesible vía la IP interna del servidor (`hostname -I`,
  ej. `10.10.0.126`) puerto `8080`. Confirmado funcionando desde Postman en otra
  máquina de la misma red.
- **Desde fuera de la red interna (IP pública)**: **todavía no habilitado** — ver
  pendientes.

## Variables de configuración importantes

| Archivo | Variable | Qué controla |
|---|---|---|
| `preprocesar.py` | `NUM_WORKERS` | Procesos en paralelo para OCR. Basado en núcleos **físicos**, no lógicos. |
| `preprocesar.py` | DPI del OCR (`get_pixmap(dpi=...)`) | 500 por defecto — necesario para leer bien dígitos en escaneos viejos/de baja calidad. |
| `chat.py` / `api_rag.py` | `TOP_K` | Cantidad de fragmentos que se le pasan a Ollama como contexto. |
| `chat.py` / `api_rag.py` | `OLLAMA_OPTIONS` | `num_ctx`, `num_predict`, `num_thread`, `temperature` — ajustar `num_thread` a núcleos físicos reales del servidor. |
| `boletines-gateway.service` | `Environment="BOLETINES_API_TOKEN=..."` | Token esperado por el gateway. Editar ahí, no en `application.properties` (queda vacío/placeholder en el repo a propósito, no versionar tokens reales). |
| `boletines-rag-gateway/application.properties` | `boletines.rag.base-url` | URL del servicio Python (default `http://127.0.0.1:8000`). |

## Pendientes conocidos (no resueltos todavía)

- **Exposición fuera de la red interna**: el puerto `8080` no está habilitado en el
  firewall/NAT perimetral de CCPM — pendiente de gestión con el área de redes.
  Confirmado con `curl` desde el propio servidor a su IP pública: timeout, no
  rechazo — es NAT/firewall externo, no un problema de la app ni del `ufw` local.
- **HTTPS**: pendiente hasta contar con un dominio para emitir certificado
  (Let's Encrypt no puede emitir certificados sobre una IP pelada). Se resuelve
  con nginx como reverse proxy delante de Spring Boot una vez haya dominio.
- **Detección de tipo de acto (decreto/resolución/ley + número)** se probó en una
  iteración anterior pero se descartó a favor de un chunking simple y uniforme;
  el full-text + semántico compensan la falta de esa estructura.

## Notas de diseño relevantes

- El texto completo de cada fragmento se guarda siempre entero en la tabla — las
  columnas `tipo_acto`/`numero_acto`/`entidades` son metadata adicional opcional
  (hoy sin usar), nunca reemplazan ni recortan el texto original.
- `nro_boletin` está desnormalizado en `chunks` (además del `boletin_id` con FK) a
  propósito, para evitar el JOIN en el camino caliente de cada búsqueda — hay
  muchísimas más lecturas que escrituras en este sistema.
- El servicio Python (`api_rag.py`) solo escucha en `127.0.0.1` — nunca queda
  expuesto directamente; toda la seguridad hacia el exterior la maneja el gateway Java.
- El token se compara con `MessageDigest.isEqual` (tiempo constante), no con
  `String.equals`, para no filtrar el token por análisis de timing.
