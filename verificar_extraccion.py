"""
Script de auditoría: NO modifica nada, solo compara lo que hay en los PDFs
contra lo que quedó guardado en la tabla 'chunks', para detectar:
  - Boletines con menos páginas cubiertas de las que tiene el PDF real.
  - Boletines con muy poco texto por página (posible falla de OCR).
  - PDFs que ni siquiera llegaron a la tabla.
"""
import os
import fitz
import psycopg2

DB_CONFIG = {
    "dbname": "boletinDB",
    "user": "postgres",
    "password": "1234",
    "host": "127.0.0.1",
    "port": "5433",
    "options": "-c client_encoding=UTF8 -c lc_messages=C"
}

CARPETA_BOLETINES = "./boletines"
UMBRAL_CARACTERES_POR_PAGINA = 150  # debajo de esto, sospechamos extracción pobre


def conectar_db():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        print(f"⚠️ No se pudo conectar a Postgres ({e}).")
        return None


def main():
    conn = conectar_db()
    if not conn:
        return
    cursor = conn.cursor()

    print(f"{'ARCHIVO':<20} {'PAG_PDF':>8} {'PAG_DB':>8} {'CHARS_TOTAL':>12} {'CHARS/PAG':>10}  ESTADO")
    print("-" * 80)

    for archivo in sorted(os.listdir(CARPETA_BOLETINES)):
        if not archivo.endswith(".pdf"):
            continue

        ruta = os.path.join(CARPETA_BOLETINES, archivo)
        doc = fitz.open(ruta)
        paginas_pdf = doc.page_count
        doc.close()

        cursor.execute(
            """
            SELECT COUNT(DISTINCT pagina), COALESCE(SUM(LENGTH(texto)), 0)
            FROM public.chunks WHERE archivo = %s;
            """,
            (archivo,)
        )
        paginas_db, chars_total = cursor.fetchone()

        if paginas_db == 0:
            estado = "❌ SIN DATOS EN LA TABLA"
        elif paginas_db < paginas_pdf:
            estado = f"⚠️ FALTAN PÁGINAS ({paginas_pdf - paginas_db})"
        else:
            chars_por_pagina = chars_total / paginas_pdf if paginas_pdf else 0
            if chars_por_pagina < UMBRAL_CARACTERES_POR_PAGINA:
                estado = "⚠️ POCO TEXTO (revisar OCR)"
            else:
                estado = "✅ OK"

        chars_por_pagina = chars_total / paginas_pdf if paginas_pdf else 0
        print(f"{archivo:<20} {paginas_pdf:>8} {paginas_db:>8} {chars_total:>12} {chars_por_pagina:>10.0f}  {estado}")

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
