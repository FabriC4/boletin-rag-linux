import os
import re
import time
import logging
import fitz  # PyMuPDF
import cv2
import numpy as np
import pytesseract
import psycopg2
import psycopg2.extras
from multiprocessing import Pool, cpu_count

# CONFIGURACIÓN DE TESSERACT EN WINDOWS
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

DB_CONFIG = {
    "dbname": "boletinDB",
    "user": "postgres",
    "password": "1234",
    "host": "127.0.0.1",
    "port": "5433",
    "options": "-c client_encoding=UTF8 -c lc_messages=C"
}

CARPETA_BOLETINES = "./boletines"

# Usa todos los núcleos disponibles menos uno (para no trabar el resto del sistema).
# Con 16GB de RAM: si notás que la máquina empieza a usar swap o se pone muy lenta
# (Administrador de tareas > Rendimiento > Memoria), bajá este número manualmente.
NUM_WORKERS = max(1, cpu_count() - 1)

# Reciclar procesos worker cada N tareas: OCR/PyMuPDF a veces acumulan memoria
# de a poco (leaks en librerías C de bajo nivel) en corridas muy largas.
# Reiniciar el proceso worker periódicamente libera esa memoria.
TAREAS_POR_WORKER_ANTES_DE_RECICLAR = 50

# Log de errores a archivo aparte (para no perderlos entre miles de líneas de consola)
logging.basicConfig(
    filename="errores_preprocesamiento.log",
    level=logging.WARNING,
    format="%(asctime)s | %(message)s"
)

# Marcador interno para saber en qué página empieza cada bloque de texto
MARCADOR_PAGINA = "\n[[PAGINA:{n}]]\n"
PATRON_MARCADOR = re.compile(r"\[\[PAGINA:(\d+)\]\]")

# Detecta tipo de acto administrativo + número (DECRETO N° 512, RESOLUCION N* 34, etc.)
PATRON_ACTO = re.compile(
    r"(DECRETO|RESOLUCI[OÓ]N|LEY|DISPOSICI[OÓ]N|EDICTO|LICITACI[OÓ]N|ORDENANZA)\s*"
    r"N[°ºª\"]?\.?\s*[:\-]?\s*(\d+)",
    re.IGNORECASE
)

# Entidades adicionales simples (extensible por tipo de acto)
PATRON_EXPEDIENTE = re.compile(r"EXPEDIENTE\s*N[°ºª]?\.?\s*[:\-]?\s*([\d./\-]+)", re.IGNORECASE)

# Diccionario chico de correcciones OCR conocidas (sumá más a medida que aparezcan)
CORRECCIONES_OCR = {
    "coriente": "corriente",
    "cancretara": "concretara",
}

TAMAÑO_CHUNK_MAX = 1500
SOLAPAMIENTO = 200


def corregir_ocr(texto):
    for error, correccion in CORRECCIONES_OCR.items():
        texto = re.sub(re.escape(error), correccion, texto, flags=re.IGNORECASE)
    return texto


def fragmentar_texto_largo(texto, tamaño_chunk=TAMAÑO_CHUNK_MAX, solapamiento=SOLAPAMIENTO):
    """Si un acto administrativo es muy largo, lo parte en sub-fragmentos con solapamiento."""
    if len(texto) <= tamaño_chunk:
        return [texto]
    chunks = []
    inicio = 0
    while inicio < len(texto):
        fin = inicio + tamaño_chunk
        chunks.append(texto[inicio:fin])
        if fin >= len(texto):
            break
        inicio += tamaño_chunk - solapamiento
    return chunks


def extraer_entidades(texto):
    """Extrae entidades estructuradas adicionales según el contenido del acto."""
    entidades = {}
    m_expediente = PATRON_EXPEDIENTE.search(texto)
    if m_expediente:
        entidades["expediente"] = m_expediente.group(1).strip()
    return entidades


def extraer_contenido_pdf(ruta_pdf):
    """Lee el PDF (texto nativo u OCR) y arma un único string con marcadores de página."""
    doc = fitz.open(ruta_pdf)
    nombre_archivo = os.path.basename(ruta_pdf)
    partes = []
    tipos_por_pagina = {}

    conectores_espanol = [" de ", " que ", " en ", " el ", " la ", " los ", " por ", " para "]

    for num_pagina, pagina in enumerate(doc):
        texto_nativo = pagina.get_text().strip()
        es_texto_valido = len(texto_nativo) > 200 and any(c in texto_nativo.lower() for c in conectores_espanol)

        if es_texto_valido:
            texto_final = texto_nativo
            tipo_extraccion = "nativo"
        else:
            pix = pagina.get_pixmap(dpi=200)
            imagen_bytes = pix.tobytes("png")
            nparr = np.frombuffer(imagen_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            img_limpia = cv2.adaptiveThreshold(
                gris, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 4
            )
            texto_final = pytesseract.image_to_string(img_limpia, lang='spa', config='--psm 3')
            tipo_extraccion = "ocr"

        texto_final = corregir_ocr(texto_final.strip())
        tipos_por_pagina[num_pagina + 1] = tipo_extraccion
        partes.append(MARCADOR_PAGINA.format(n=num_pagina + 1))
        partes.append(texto_final)

    texto_completo = "".join(partes)
    return nombre_archivo, texto_completo, tipos_por_pagina


def pagina_en_posicion(texto_completo, posicion):
    """Dada una posición de caracter en texto_completo, devuelve el número de página más cercano hacia atrás."""
    pagina_actual = 1
    for m in PATRON_MARCADOR.finditer(texto_completo):
        if m.start() > posicion:
            break
        pagina_actual = int(m.group(1))
    return pagina_actual


def dividir_por_acto(texto_completo):
    """Divide el texto completo del documento en bloques, uno por cada acto administrativo detectado."""
    matches = list(PATRON_ACTO.finditer(texto_completo))
    bloques = []

    if not matches:
        texto_limpio = PATRON_MARCADOR.sub(" ", texto_completo).strip()
        if texto_limpio:
            bloques.append({"tipo_acto": None, "numero_acto": None, "texto": texto_limpio, "pagina": 1})
        return bloques

    if matches[0].start() > 0:
        preambulo = PATRON_MARCADOR.sub(" ", texto_completo[:matches[0].start()]).strip()
        if len(preambulo) > 50:
            bloques.append({"tipo_acto": None, "numero_acto": None, "texto": preambulo, "pagina": 1})

    for i, m in enumerate(matches):
        inicio = m.start()
        fin = matches[i + 1].start() if i + 1 < len(matches) else len(texto_completo)
        pagina = pagina_en_posicion(texto_completo, inicio)
        texto_bloque = PATRON_MARCADOR.sub(" ", texto_completo[inicio:fin]).strip()
        texto_bloque = " ".join(texto_bloque.split())

        bloques.append({
            "tipo_acto": m.group(1).lower().replace("ó", "o"),
            "numero_acto": m.group(2),
            "texto": texto_bloque,
            "pagina": pagina
        })

    return bloques


def procesar_pdf_worker(args):
    """Se ejecuta en un proceso worker aparte: SOLO extracción/CPU, nunca toca la DB."""
    archivo, ruta_completa, boletin_id, nro_boletin = args
    try:
        nombre_pdf, texto_completo, tipos_por_pagina = extraer_contenido_pdf(ruta_completa)
        bloques = dividir_por_acto(texto_completo)

        filas = []
        for bloque in bloques:
            sub_fragmentos = fragmentar_texto_largo(bloque["texto"])
            for i, sub_texto in enumerate(sub_fragmentos):
                if not sub_texto.strip():
                    continue
                entidades = extraer_entidades(sub_texto)
                tipo_extraccion = tipos_por_pagina.get(bloque["pagina"], "nativo")
                filas.append((
                    boletin_id, nro_boletin, nombre_pdf, bloque["pagina"], i + 1,
                    bloque["tipo_acto"], bloque["numero_acto"],
                    psycopg2.extras.Json(entidades), tipo_extraccion, sub_texto
                ))

        return {"ok": True, "archivo": archivo, "nro_boletin": nro_boletin,
                "filas": filas, "actos": len(bloques)}
    except Exception as e:
        return {"ok": False, "archivo": archivo, "nro_boletin": nro_boletin, "error": str(e)}


def conectar_db():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except UnicodeDecodeError as e:
        print(f"⚠️ Postgres devolvió un error no legible (bytes: {e.object!r}).")
        return None
    except Exception as e:
        print(f"⚠️ No se pudo conectar a Postgres ({e}).")
        return None


def obtener_boletin(cursor, nombre_archivo):
    cursor.execute(
        "SELECT id, nro_boletin FROM public.boletines WHERE patharchivo LIKE %s;",
        (f"%{nombre_archivo}",)
    )
    return cursor.fetchone()


def ya_procesado(cursor, nro_boletin):
    cursor.execute("SELECT COUNT(*) FROM public.chunks WHERE nro_boletin = %s;", (nro_boletin,))
    return cursor.fetchone()[0] > 0


def obtener_boletines_procesados(cursor):
    """Trae en UNA sola consulta todos los nro_boletin que ya tienen chunks,
    en vez de hacer una query por cada uno de los 20k archivos."""
    cursor.execute("SELECT DISTINCT nro_boletin FROM public.chunks;")
    return {fila[0] for fila in cursor.fetchall()}


def guardar_filas(cursor, filas):
    if not filas:
        return
    psycopg2.extras.execute_values(
        cursor,
        """
        INSERT INTO public.chunks
            (boletin_id, nro_boletin, archivo, pagina, fragmento_nro,
             tipo_acto, numero_acto, entidades, tipo_extraccion, texto)
        VALUES %s
        ON CONFLICT (nro_boletin, pagina, fragmento_nro) DO NOTHING
        """,
        filas
    )


def main():
    if not os.path.exists(CARPETA_BOLETINES):
        print(f"❌ Error: No se encuentra la carpeta '{CARPETA_BOLETINES}'")
        return

    print("🔌 Conectando a la base de datos de Docker...")
    conn = conectar_db()
    if not conn:
        print("❌ No se puede continuar sin conexión a la base.")
        return
    print("✅ Conectado a Postgres correctamente.")
    cursor = conn.cursor()

    # --- Fase de planificación (rápida: 1 sola query para saber qué ya está cargado) ---
    print("📋 Revisando qué boletines ya están cargados...")
    procesados = obtener_boletines_procesados(cursor)
    print(f"   {len(procesados)} boletines ya estaban en la base.")

    lista_trabajo = []
    saltados_sin_match = 0
    archivos_pdf = sorted(f for f in os.listdir(CARPETA_BOLETINES) if f.endswith(".pdf"))
    print(f"📂 {len(archivos_pdf)} PDFs encontrados en la carpeta. Verificando cada uno contra la DB...")

    for archivo in archivos_pdf:
        resultado = obtener_boletin(cursor, archivo)
        if not resultado:
            saltados_sin_match += 1
            logging.warning(f"Sin coincidencia en la DB para el archivo: {archivo}")
            continue

        boletin_id, nro_boletin = resultado
        if nro_boletin in procesados:
            continue

        ruta_completa = os.path.join(CARPETA_BOLETINES, archivo)
        lista_trabajo.append((archivo, ruta_completa, boletin_id, nro_boletin))

    print(f"   {saltados_sin_match} archivos sin coincidencia en 'boletines' (ver errores_preprocesamiento.log).")
    print(f"   {len(archivos_pdf) - saltados_sin_match - len(lista_trabajo)} ya estaban cargados, se omiten.")

    if not lista_trabajo:
        print("\n✅ No hay boletines nuevos para procesar.")
        cursor.close()
        conn.close()
        return

    print(f"\n🚀 Procesando {len(lista_trabajo)} boletines en paralelo con {NUM_WORKERS} procesos...\n")

    total_chunks = 0
    total_ok = 0
    total_error = 0
    inicio = time.time()

    # --- Fase de extracción (paralela) + escritura (secuencial, con reconexión ante caídas) ---
    with Pool(processes=NUM_WORKERS, maxtasksperchild=TAREAS_POR_WORKER_ANTES_DE_RECICLAR) as pool:
        for i, resultado in enumerate(pool.imap_unordered(procesar_pdf_worker, lista_trabajo), start=1):

            if not resultado["ok"]:
                logging.warning(f"Error procesando {resultado['archivo']}: {resultado['error']}")
                total_error += 1
            else:
                guardado = False
                for intento in range(2):  # 1 reintento si la conexión se cayó
                    try:
                        guardar_filas(cursor, resultado["filas"])
                        conn.commit()
                        guardado = True
                        break
                    except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
                        logging.warning(f"Conexión perdida guardando {resultado['archivo']} ({e}). Reconectando...")
                        conn = conectar_db()
                        if not conn:
                            logging.warning("No se pudo reconectar. Se reintentará este boletín en la próxima corrida.")
                            break
                        cursor = conn.cursor()
                    except Exception as e:
                        logging.warning(f"Error guardando {resultado['archivo']} en la DB: {e}")
                        conn.rollback()
                        break

                if guardado:
                    total_chunks += len(resultado["filas"])
                    total_ok += 1
                else:
                    total_error += 1

            # Progreso cada 25 archivos (no saturar la consola en corridas de miles)
            if i % 25 == 0 or i == len(lista_trabajo):
                transcurrido = time.time() - inicio
                velocidad = i / transcurrido if transcurrido > 0 else 0
                restantes = len(lista_trabajo) - i
                eta_min = (restantes / velocidad / 60) if velocidad > 0 else 0
                print(f"   [{i}/{len(lista_trabajo)}] ok={total_ok} error={total_error} "
                      f"chunks={total_chunks} | {velocidad:.2f} boletines/seg | ETA ~{eta_min:.0f} min")

    cursor.close()
    conn.close()
    minutos_totales = (time.time() - inicio) / 60
    print(f"\n✅ ¡Listo! {total_ok} boletines procesados, {total_error} con error "
          f"(detalle en errores_preprocesamiento.log), {total_chunks} chunks nuevos guardados.")
    print(f"⏱️  Tiempo total: {minutos_totales:.1f} minutos.")


if __name__ == "__main__":
    main()
