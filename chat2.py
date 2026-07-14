import re
import urllib.request
import json
import psycopg2
import psycopg2.extras
from sentence_transformers import SentenceTransformer

# Palabras "meta" de la pregunta (no son contenido real de los boletines, son parte de
# cómo la gente formula preguntas). El diccionario en español de Postgres no las filtra
# porque son válidas en otros contextos, pero acá solo generan ruido: "boletin" aparece
# en el encabezado de CASI TODAS las páginas, así que buscarla no discrimina nada.
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


def limpiar_pregunta_para_busqueda(pregunta):
    """Saca palabras de relleno conversacional antes de armar la tsquery, para no
    diluir la búsqueda con términos genéricos que no aportan a encontrar el contenido real."""
    palabras = re.findall(r"\w+", pregunta.lower())
    palabras_limpias = [p for p in palabras if p not in PALABRAS_RELLENO]
    resultado = " ".join(palabras_limpias)
    return resultado if resultado.strip() else pregunta  # si limpiamos todo, usamos la original

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

TOP_K = 6  # bajado de 8: menos texto que Ollama tiene que "leer" antes de responder
MAX_TURNOS_MEMORIA = 3

# Ajustá NUM_THREAD_OLLAMA a tus núcleos físicos reales (nproc te dice los lógicos;
# para inferencia, un valor cercano a los físicos suele andar mejor que el máximo).
OLLAMA_OPTIONS = {
    "num_ctx": 6144,      # contexto: TOP_K=6 fragmentos de ~1000 chars + historial + prompt entra cómodo
    "num_predict": 700,   # tope de tokens de respuesta (subilo si necesitás respuestas más largas)
    "temperature": 0.3,   # bajo = más apegado al contexto, menos alucinación
    "num_thread": 40,     # igual al NUM_WORKERS que usás en preprocesar.py (núcleos físicos)
    "num_batch": 512,
}


def conectar_db():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except UnicodeDecodeError as e:
        print(f"⚠️ Postgres devolvió un error no legible (bytes: {e.object!r}).")
        return None
    except Exception as e:
        print(f"⚠️ No se pudo conectar a Postgres ({e}).")
        return None


def buscar_nivel1_fulltext(cursor, pregunta, limite):
    """Full-text estricto: TODAS las palabras clave deben aparecer en el mismo chunk (AND).
    Máxima precisión, pero falla con preguntas conversacionales tipo 'boletines que hablen de X'
    (esas palabras de relleno nunca van a matchear juntas con el contenido real)."""
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
    """Full-text flexible: CUALQUIERA de las palabras clave (OR), no todas.
    Se usa como red de seguridad cuando el AND estricto no alcanza. Reusa el diccionario
    en español de Postgres (plainto_tsquery) para mantener el mismo stemming/stopwords,
    solo cambiamos los conectores de AND (&) a OR (|)."""
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
    """Búsqueda vectorial con pgvector, para preguntas conceptuales que no comparten palabras exactas."""
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
    """Combina full-text estricto + full-text flexible + semántico, sin duplicar resultados."""
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
        # Acá SÍ usamos la pregunta original completa: los embeddings semánticos
        # están entrenados con oraciones naturales, sacarle palabras no ayuda.
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
3. IMPORTANTE: cada PREGUNTA DEL USUARIO es un tema independiente y nuevo, salvo que use una referencia explícita a la conversación anterior (por ejemplo "y quién lo firmó?", "¿y ese decreto...?", "eso mismo pero..."). Si la pregunta actual no tiene ninguna palabra que la conecte con la conversación previa, IGNORÁ COMPLETAMENTE la conversación previa y respondé solo en base al CONTEXTO de boletines actual. No relaciones ni mezcles información de una pregunta anterior con la pregunta actual si no hay una conexión explícita.
{bloque_historial}
CONTEXTO DE LOS BOLETINES:
{contexto}

PREGUNTA DEL USUARIO:
{pregunta}

RESPUESTA:"""

    payload = {
        "model": MODELO_OLLAMA,
        "prompt": prompt_sistema,
        "stream": True,  # mostramos la respuesta a medida que se genera (se siente mucho más rápido)
        "keep_alive": "30m",  # el modelo queda cargado en RAM, no se recarga en cada pregunta
        "options": OLLAMA_OPTIONS
    }
    headers = {'Content-Type': 'application/json'}
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)

    respuesta_completa = ""
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            for linea in response:
                if not linea.strip():
                    continue
                data = json.loads(linea.decode('utf-8'))
                trozo = data.get("response", "")
                print(trozo, end="", flush=True)
                respuesta_completa += trozo
                if data.get("done"):
                    break
        print()
        return respuesta_completa
    except Exception as e:
        return f"\n❌ Error al conectar con Ollama ({e})."


def main():
    print("🔌 Conectando a la base de datos de Docker...")
    conn = conectar_db()
    if not conn:
        print("❌ No se puede continuar sin conexión a la base.")
        return
    print("✅ Conectado a Postgres correctamente.")
    cursor = conn.cursor()

    print("⏳ Cargando el modelo de embeddings...")
    modelo_embeddings = SentenceTransformer(MODELO_EMBEDDINGS)

    print("\n🚀 ¡Buscador Híbrido (Postgres + pgvector) Listo!")
    print("Para salir, escribí 'salir'.\n")

    historial = []

    while True:
        usuario_input = input("👤 Tu pregunta: ")
        if usuario_input.strip().lower() == "salir":
            print("¡Nos vemos!")
            break
        if not usuario_input.strip():
            continue

        print("🔍 Buscando...")
        try:
            fragmentos = buscar_fragmentos(cursor, modelo_embeddings, usuario_input)
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            print("⚠️ Se perdió la conexión. Reconectando...")
            conn = conectar_db()
            if not conn:
                print("❌ No se pudo reconectar.")
                break
            cursor = conn.cursor()
            continue

        print("\n🧪 DEBUG - Fragmentos recuperados:")
        for r in fragmentos:
            rango_pag = f"{r['pagina']}" if r['pagina'] == r['pagina_fin'] else f"{r['pagina']}-{r['pagina_fin']}"
            print(f"   boletin={r['nro_boletin']} | archivo={r['archivo']} | pag={rango_pag} | "
                  f"tipo={r['tipo_extraccion']} | preview={r['texto'][:60]!r}")
        print()

        contexto_bloque = ""
        fuentes = []
        for i, r in enumerate(fragmentos):
            contexto_bloque += f"--- Fragmento {i+1} (Boletín Nro: {r['nro_boletin']}) ---\n{r['texto']}\n\n"
            rango_pag = f"{r['pagina']}" if r['pagina'] == r['pagina_fin'] else f"{r['pagina']}-{r['pagina_fin']}"
            fuentes.append(
                f"📌 [Fuente] Boletín Nro: {r['nro_boletin']} | Archivo: {r['archivo']} | Página: {rango_pag}"
            )

        print("\n🤖 Respuesta de la IA:")
        respuesta_ia = preguntar_a_ollama(usuario_input, contexto_bloque, historial[-MAX_TURNOS_MEMORIA:])

        print("\n📄 Documentación de respaldo utilizada:")
        for f in fuentes:
            print(f)
        print("-" * 60 + "\n")

        historial.append({"pregunta": usuario_input, "respuesta": respuesta_ia})

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
