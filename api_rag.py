"""
Servicio interno HTTP para el RAG de boletines.

Este servicio NO se expone directamente a internet. Solo el backend Java (Spring
Boot) le habla, desde la misma máquina o red interna. La seguridad (tokens) la
maneja Java; este servicio confía en que solo Java le llega tráfico.

Reutiliza exactamente la misma lógica de búsqueda de chat.py (Nivel 1 full-text
estricto, Nivel 1b flexible, Nivel 2 semántico) y de generación con Ollama, sin
cambios de comportamiento — la diferencia es que el modelo de embeddings y la
conexión a Postgres se cargan UNA sola vez al arrancar el proceso, y quedan
"calientes" atendiendo pedidos por HTTP, en vez de recargarse en cada corrida
de consola.

Correr con:
    uvicorn api_rag:app --host 127.0.0.1 --port 8000 --workers 1

IMPORTANTE: --workers 1 (no más). El modelo de embeddings y la conexión a
Postgres se cargan en memoria del proceso; con más de 1 worker se duplicarían
innecesariamente. Si hace falta atender más carga concurrente, se resuelve con
un pool de conexiones a Postgres (ver nota al final), no con más workers de uvicorn.
"""
import re
import json
import urllib.request
import psycopg2
import psycopg2.extras
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

DB_CONFIG = {
    "dbname": "boletinDB",
    "user": "postgres",
    "password": "1234",
    "host": "127.0.0.1",
    "port": "5433",
    "options": "-c client_encoding=UTF8 -c lc_messages=C"
}

MODELO_OLLAMA = "qwen2.5"
MODELO_EMBEDDINGS = "paraphrase-multilingual-MiniLM-L12-v2"
TOP_K = 4
MAX_TURNOS_MEMORIA = 3

OLLAMA_OPTIONS = {
    "num_ctx": 4096,
    "num_predict": 400,
    "temperature": 0.3,
    "num_thread": 18,
    "num_batch": 512,
}

PALABRAS_RELLENO = {
    "boletin", "boletines", "boletín",
    "hablen", "hable", "habla", "hablar", "hablando",
    "encuentra", "encuentran", "encontrar",
    "menciona", "mencionan", "mencionen", "mencione",
    "aparece", "aparecen", "aparezca",
    "dice", "dicen", "diga", "digan",
    "existe", "existen", "hay",
    "sobre", "acerca", "informacion", "información", "info",
}

# --- Estado global del proceso: se inicializa una sola vez en el startup ---
estado = {"conn": None, "cursor": None, "modelo_embeddings": None}


def limpiar_pregunta_para_busqueda(pregunta):
    palabras = re.findall(r"\w+", pregunta.lower())
    palabras_limpias = [p for p in palabras if p not in PALABRAS_RELLENO]
    resultado = " ".join(palabras_limpias)
    return resultado if resultado.strip() else pregunta


def conectar_db():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        print(f"⚠️ No se pudo conectar a Postgres ({e}).")
        return None


def buscar_nivel1_fulltext(cursor, pregunta, limite):
    cursor.execute(
        """
        SELECT id, texto, nro_boletin, archivo, pagina, pagina_fin, tipo_extraccion,
               ts_rank(texto_busqueda, plainto_tsquery('spanish', %s)) AS rank
        FROM public.chunks
        WHERE texto_busqueda @@ plainto_tsquery('spanish', %s)
          AND NOT (texto ILIKE '%%SUMARIO%%' AND texto ILIKE '%%Decretos%%')
        ORDER BY rank DESC
        LIMIT %s;
        """,
        (pregunta, pregunta, limite)
    )
    return cursor.fetchall()


def buscar_nivel1b_fulltext_flexible(cursor, pregunta, limite, excluir_ids):
    cursor.execute("SELECT plainto_tsquery('spanish', %s)::text;", (pregunta,))
    query_and = cursor.fetchone()[0]
    if not query_and:
        return []
    query_or = query_and.replace(" & ", " | ")

    if excluir_ids:
        cursor.execute(
            """
            SELECT id, texto, nro_boletin, archivo, pagina, pagina_fin, tipo_extraccion,
                   ts_rank(texto_busqueda, to_tsquery('spanish', %s)) AS rank
            FROM public.chunks
            WHERE texto_busqueda @@ to_tsquery('spanish', %s)
              AND id != ALL(%s)
              AND NOT (texto ILIKE '%%SUMARIO%%' AND texto ILIKE '%%Decretos%%')
            ORDER BY rank DESC
            LIMIT %s;
            """,
            (query_or, query_or, list(excluir_ids), limite)
        )
    else:
        cursor.execute(
            """
            SELECT id, texto, nro_boletin, archivo, pagina, pagina_fin, tipo_extraccion,
                   ts_rank(texto_busqueda, to_tsquery('spanish', %s)) AS rank
            FROM public.chunks
            WHERE texto_busqueda @@ to_tsquery('spanish', %s)
              AND NOT (texto ILIKE '%%SUMARIO%%' AND texto ILIKE '%%Decretos%%')
            ORDER BY rank DESC
            LIMIT %s;
            """,
            (query_or, query_or, limite)
        )
    return cursor.fetchall()


def buscar_nivel2_semantico(cursor, modelo_embeddings, pregunta, limite, excluir_ids):
    vector = modelo_embeddings.encode(pregunta, convert_to_numpy=True).tolist()
    if excluir_ids:
        cursor.execute(
            """
            SELECT id, texto, nro_boletin, archivo, pagina, pagina_fin, tipo_extraccion,
                   1 - (embedding <=> %s::vector) AS similitud
            FROM public.chunks
            WHERE embedding IS NOT NULL AND id != ALL(%s)
            ORDER BY embedding <=> %s::vector
            LIMIT %s;
            """,
            (vector, list(excluir_ids), vector, limite)
        )
    else:
        cursor.execute(
            """
            SELECT id, texto, nro_boletin, archivo, pagina, pagina_fin, tipo_extraccion,
                   1 - (embedding <=> %s::vector) AS similitud
            FROM public.chunks
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s;
            """,
            (vector, vector, limite)
        )
    return cursor.fetchall()


def fila_a_dict(fila):
    return {
        "id": fila[0], "texto": fila[1], "nro_boletin": fila[2], "archivo": fila[3],
        "pagina": fila[4], "pagina_fin": fila[5], "tipo_extraccion": fila[6]
    }


def buscar_fragmentos(cursor, modelo_embeddings, pregunta, top_k=TOP_K):
    resultados = []
    ids_usados = set()
    pregunta_limpia = limpiar_pregunta_para_busqueda(pregunta)

    for fila in buscar_nivel1_fulltext(cursor, pregunta_limpia, top_k):
        resultados.append(fila_a_dict(fila))
        ids_usados.add(fila[0])

    if len(resultados) < top_k:
        faltan = top_k - len(resultados)
        for fila in buscar_nivel1b_fulltext_flexible(cursor, pregunta_limpia, faltan, ids_usados):
            resultados.append(fila_a_dict(fila))
            ids_usados.add(fila[0])

    if len(resultados) < top_k:
        faltan = top_k - len(resultados)
        for fila in buscar_nivel2_semantico(cursor, modelo_embeddings, pregunta, faltan, ids_usados):
            resultados.append(fila_a_dict(fila))
            ids_usados.add(fila[0])

    return resultados


def preguntar_a_ollama(pregunta, contexto, historial):
    url = "http://localhost:11434/api/generate"

    bloque_historial = ""
    if historial:
        turnos = "\n".join(f"Usuario: {h['pregunta']}\nAsistente: {h['respuesta']}" for h in historial)
        bloque_historial = f"\nCONVERSACIÓN PREVIA (para dar contexto a preguntas de seguimiento):\n{turnos}\n"

    prompt_sistema = f"""Sos un asistente experto en análisis de Boletines Oficiales.
Tu tarea es responder la pregunta del usuario utilizando ÚNICAMENTE los fragmentos de los boletines oficiales provistos en el CONTEXTO.

Reglas estrictas:
1. Sé preciso, formal y cita textualmente si es necesario.
2. Si en el contexto no figura la respuesta o no estás seguro, decí amablemente: "No encontré información sobre ese tema en los boletines cargados". No inventes nada.
3. IMPORTANTE: cada PREGUNTA DEL USUARIO es un tema independiente y nuevo, salvo que use una referencia explícita a la conversación anterior. Si la pregunta actual no tiene ninguna palabra que la conecte con la conversación previa, IGNORÁ COMPLETAMENTE la conversación previa.
{bloque_historial}
CONTEXTO DE LOS BOLETINES:
{contexto}

PREGUNTA DEL USUARIO:
{pregunta}

RESPUESTA:"""

    payload = {
        "model": MODELO_OLLAMA,
        "prompt": prompt_sistema,
        "stream": False,  # acá NO usamos streaming: Java espera la respuesta completa en el JSON
        "keep_alive": "30m",
        "options": OLLAMA_OPTIONS
    }
    headers = {'Content-Type': 'application/json'}
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            res_json = json.loads(response.read().decode('utf-8'))
            return res_json.get("response", "")
    except Exception as e:
        raise RuntimeError(f"Error al conectar con Ollama: {e}")


# --- Ciclo de vida del servicio: cargar modelo y conectar DB una sola vez ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("⏳ Cargando modelo de embeddings...")
    estado["modelo_embeddings"] = SentenceTransformer(MODELO_EMBEDDINGS)
    print("🔌 Conectando a Postgres...")
    estado["conn"] = conectar_db()
    if estado["conn"] is None:
        raise RuntimeError("No se pudo conectar a Postgres al arrancar el servicio.")
    estado["cursor"] = estado["conn"].cursor()
    print("✅ Servicio RAG listo para atender pedidos.")
    yield
    estado["cursor"].close()
    estado["conn"].close()
    print("🔌 Conexión cerrada, servicio detenido.")


app = FastAPI(title="Boletines RAG - Servicio Interno", lifespan=lifespan)


class ConsultaRequest(BaseModel):
    pregunta: str
    historial: list[dict] = []  # [{"pregunta": "...", "respuesta": "..."}, ...] opcional


class Fuente(BaseModel):
    nro_boletin: int
    archivo: str
    pagina: int
    pagina_fin: int


class ConsultaResponse(BaseModel):
    respuesta: str
    fuentes: list[Fuente]


@app.post("/consultar", response_model=ConsultaResponse)
def consultar(body: ConsultaRequest):
    if not body.pregunta.strip():
        raise HTTPException(status_code=400, detail="La pregunta no puede estar vacía.")

    try:
        fragmentos = buscar_fragmentos(estado["cursor"], estado["modelo_embeddings"], body.pregunta)
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        # Reconexión ante conexión caída, igual que en chat.py
        estado["conn"] = conectar_db()
        if estado["conn"] is None:
            raise HTTPException(status_code=503, detail="No se pudo reconectar a la base de datos.")
        estado["cursor"] = estado["conn"].cursor()
        fragmentos = buscar_fragmentos(estado["cursor"], estado["modelo_embeddings"], body.pregunta)

    contexto_bloque = ""
    fuentes = []
    for i, r in enumerate(fragmentos):
        contexto_bloque += f"--- Fragmento {i+1} (Boletín Nro: {r['nro_boletin']}) ---\n{r['texto']}\n\n"
        fuentes.append(Fuente(
            nro_boletin=r["nro_boletin"], archivo=r["archivo"],
            pagina=r["pagina"], pagina_fin=r["pagina_fin"]
        ))

    try:
        respuesta = preguntar_a_ollama(body.pregunta, contexto_bloque, body.historial[-MAX_TURNOS_MEMORIA:])
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return ConsultaResponse(respuesta=respuesta, fuentes=fuentes)


@app.get("/salud")
def salud():
    """Para que Java (o cualquier monitor) chequee si el servicio está vivo."""
    return {"estado": "ok"}
