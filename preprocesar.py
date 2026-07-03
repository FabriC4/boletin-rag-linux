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
NUM_WORKERS = max(1, cpu_count() - 1)
TAREAS_POR_WORKER_ANTES_DE_RECICLAR = 50

TAMAÑO_CHUNK = 1000
SOLAPAMIENTO = 200

# Diccionario chico de correcciones OCR conocidas (sumá más a medida que aparezcan)
CORRECCIONES_OCR = {
    "coriente": "corriente",
    "cancretara": "concretara",
}

logging.basicConfig(
    filename="errores_preprocesamiento.log",
    level=logging.WARNING,
    format="%(asctime)s | %(message)s"
)


def corregir_ocr(texto):
    for error, correccion in CORRECCIONES_OCR.items():
        texto = re.sub(re.escape(error), correccion, texto, flags=re.IGNORECASE)
    return texto


def fragmentar_texto(texto, tamaño_chunk=TAMAÑO_CHUNK, solapamiento=SOLAPAMIENTO):
    """Divide el texto en pedazos más chicos con solapamiento, sin importar el contenido."""
    chunks = []
    inicio = 0
    while inicio < len(texto):
        fin = inicio + tamaño_chunk
        chunks.append(texto[inicio:fin])
        if fin >= len(texto):
            break
        inicio += tamaño_chunk - solapamiento
    return chunks


def extraer_contenido_pdf(ruta_pdf):
    """Lee el PDF página por página: texto nativo, OCR, o ambos combinados si la
    página tiene texto tipeado Y además imágenes (sellos, firmas, anexos escaneados)."""
    doc = fitz.open(ruta_pdf)
    nombre_archivo = os.path.basename(ruta_pdf)
    paginas_resultado = []

    conectores_espanol = [" de ", " que ", " en ", " el ", " la ", " los ", " por ", " para "]

    for num_pagina, pagina in enumerate(doc):
        texto_nativo = pagina.get_text().strip()
        nativo_valido = len(texto_nativo) > 200 and any(c in texto_nativo.lower() for c in conectores_espanol)
        tiene_imagenes = len(pagina.get_images(full=True)) > 0

        texto_ocr = ""
        if tiene_imagenes or not nativo_valido:
            pix = pagina.get_pixmap(dpi=500)
            imagen_bytes = pix.tobytes("png")
            nparr = np.frombuffer(imagen_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            img_limpia = cv2.adaptiveThreshold(
                gris, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 6
            )
            texto_ocr = pytesseract.image_to_string(img_limpia, lang='spa', config='--psm 3').strip()

        if nativo_valido and texto_ocr:
            texto_final = texto_nativo + "\n\n[Texto detectado en imagen de la misma página]\n" + texto_ocr
            tipo_extraccion = "hibrido"
        elif nativo_valido:
            texto_final = texto_nativo
            tipo_extraccion = "nativo"
        elif texto_ocr:
            texto_final = texto_ocr
            tipo_extraccion = "ocr"
        else:
            texto_final = texto_nativo
            tipo_extraccion = "nativo"

        texto_final = corregir_ocr(texto_final.strip())
        paginas_resultado.append({
            "pagina": num_pagina + 1,
            "texto": texto_final,
            "tipo": tipo_extraccion
        })

    return nombre_archivo, paginas_resultado


def procesar_pdf_worker(args):
    """Se ejecuta en un proceso worker aparte: SOLO extracción/CPU, nunca toca la DB."""
    archivo, ruta_completa, boletin_id, nro_boletin = args
    try:
        nombre_pdf, paginas = extraer_contenido_pdf(ruta_completa)

        filas = []
        for p in paginas:
            if not p["texto"]:
                continue
            sub_fragmentos = fragmentar_texto(p["texto"])
            for i, sub_texto in enumerate(sub_fragmentos):
                if not sub_texto.strip():
                    continue
                filas.append((
                    boletin_id, nro_boletin, nombre_pdf, p["pagina"], p["pagina"], i + 1,
                    None, None, psycopg2.extras.Json({}), p["tipo"], sub_texto
                ))

        return {"ok": True, "archivo": archivo, "nro_boletin": nro_boletin,
                "filas": filas, "paginas": len(paginas)}
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


def obtener_boletines_procesados(cursor):
    cursor.execute("SELECT DISTINCT nro_boletin FROM public.chunks;")
    return {fila[0] for fila in cursor.fetchall()}


def guardar_filas(cursor, filas):
    if not filas:
        return
    psycopg2.extras.execute_values(
        cursor,
        """
        INSERT INTO public.chunks
            (boletin_id, nro_boletin, archivo, pagina, pagina_fin, fragmento_nro,
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

    print("📋 Revisando qué boletines ya están cargados...")
    procesados = obtener_boletines_procesados(cursor)
    print(f"   {len(procesados)} boletines ya estaban en la base.")

    lista_trabajo = []
    saltados_sin_match = 0
    archivos_pdf = sorted(f for f in os.listdir(CARPETA_BOLETINES) if f.endswith(".pdf"))
    print(f"📂 {len(archivos_pdf)} PDFs encontrados en la carpeta.")

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

    with Pool(processes=NUM_WORKERS, maxtasksperchild=TAREAS_POR_WORKER_ANTES_DE_RECICLAR) as pool:
        for i, resultado in enumerate(pool.imap_unordered(procesar_pdf_worker, lista_trabajo), start=1):

            if not resultado["ok"]:
                logging.warning(f"Error procesando {resultado['archivo']}: {resultado['error']}")
                total_error += 1
            else:
                guardado = False
                for intento in range(2):
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
