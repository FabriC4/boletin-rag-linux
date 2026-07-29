"""
Arma, por cada boletín, un JSON con el texto completo (reconstruido a partir de los
chunks, sin los 200 caracteres de solapamiento repetidos entre fragmentos) y lo
guarda en la tabla `boletines_texto_completo` (una fila por boletín, columna JSONB).

Es incremental: salta los boletines que ya están exportados (a menos que uses --forzar).

Uso:
    python3 exportar_texto_completo.py            # solo los nuevos
    python3 exportar_texto_completo.py --forzar   # reexporta todos, pisando lo que había
"""
import sys
import json
import psycopg2
import psycopg2.extras

DB_CONFIG = {
    "dbname": "boletinDB",
    "user": "postgres",
    "password": "1234",
    "host": "127.0.0.1",
    "port": "5433",
    "options": "-c client_encoding=UTF8 -c lc_messages=C"
}

SOLAPAMIENTO = 200  # tiene que coincidir con el SOLAPAMIENTO de preprocesar.py


def conectar_db():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except Exception as e:
        print(f"⚠️ No se pudo conectar a Postgres ({e}).")
        return None


def obtener_boletines_ya_exportados(cursor):
    cursor.execute("SELECT nro_boletin FROM public.boletines WHERE texto_json IS NOT NULL;")
    return {fila[0] for fila in cursor.fetchall()}


def reconstruir_texto_pagina(fragmentos_ordenados):
    """Junta los sub-fragmentos de una misma página sacando el solapamiento repetido.
    fragmentos_ordenados: lista de texto, ya ordenada por fragmento_nro."""
    if not fragmentos_ordenados:
        return ""
    texto = fragmentos_ordenados[0]
    for frag in fragmentos_ordenados[1:]:
        # Cada fragmento siguiente repite los primeros SOLAPAMIENTO caracteres
        # del anterior -- los salteamos al pegarlo.
        texto += frag[SOLAPAMIENTO:] if len(frag) > SOLAPAMIENTO else frag
    return texto


def procesar_boletin(cursor, nro_boletin, archivo, filas_del_boletin):
    """filas_del_boletin: lista de (pagina, fragmento_nro, tipo_extraccion, texto), ordenada."""
    paginas = {}
    for pagina, fragmento_nro, tipo_extraccion, texto in filas_del_boletin:
        paginas.setdefault(pagina, {"tipo_extraccion": tipo_extraccion, "fragmentos": []})
        paginas[pagina]["fragmentos"].append((fragmento_nro, texto))

    paginas_json = []
    texto_completo_partes = []
    for num_pagina in sorted(paginas.keys()):
        info = paginas[num_pagina]
        fragmentos_ordenados = [t for _, t in sorted(info["fragmentos"], key=lambda x: x[0])]
        texto_pagina = reconstruir_texto_pagina(fragmentos_ordenados)
        paginas_json.append({
            "pagina": num_pagina,
            "tipo_extraccion": info["tipo_extraccion"],
            "texto": texto_pagina
        })
        texto_completo_partes.append(texto_pagina)

    datos = {
        "nro_boletin": nro_boletin,
        "archivo": archivo,
        "total_paginas": len(paginas_json),
        "paginas": paginas_json,
        "texto_completo": "\n\n".join(texto_completo_partes)
    }

    cursor.execute(
        """
        UPDATE public.boletines
        SET texto_json = %s
        WHERE nro_boletin = %s;
        """,
        (psycopg2.extras.Json(datos), nro_boletin)
    )


def main():
    forzar = "--forzar" in sys.argv

    conn_lectura = conectar_db()
    conn_escritura = conectar_db()
    if not conn_lectura or not conn_escritura:
        return
    cursor_lectura = conn_lectura.cursor(name="cursor_chunks")  # server-side, no carga todo en RAM
    cursor_escritura = conn_escritura.cursor()

    ya_exportados = set() if forzar else obtener_boletines_ya_exportados(cursor_escritura)
    print(f"📋 {len(ya_exportados)} boletines ya exportados (se omiten, salvo --forzar).")

    cursor_lectura.execute(
        """
        SELECT nro_boletin, archivo, pagina, fragmento_nro, tipo_extraccion, texto
        FROM public.chunks
        ORDER BY nro_boletin, pagina, fragmento_nro;
        """
    )

    boletin_actual = None
    archivo_actual = None
    filas_del_boletin = []
    total_procesados = 0
    total_saltados = 0

    def cerrar_boletin_actual():
        nonlocal total_procesados
        if boletin_actual is None or not filas_del_boletin:
            return
        if boletin_actual in ya_exportados:
            return
        procesar_boletin(cursor_escritura, boletin_actual, archivo_actual, filas_del_boletin)
        total_procesados += 1
        if total_procesados % 100 == 0:
            conn_escritura.commit()  # solo comitea la conexión de escritura, no toca la de lectura
            print(f"   {total_procesados} boletines exportados...")

    for nro_boletin, archivo, pagina, fragmento_nro, tipo_extraccion, texto in cursor_lectura:
        if nro_boletin != boletin_actual:
            cerrar_boletin_actual()
            if boletin_actual in ya_exportados:
                total_saltados += 1
            boletin_actual = nro_boletin
            archivo_actual = archivo
            filas_del_boletin = []
        filas_del_boletin.append((pagina, fragmento_nro, tipo_extraccion, texto))

    cerrar_boletin_actual()  # el último boletín del recorrido
    if boletin_actual in ya_exportados:
        total_saltados += 1

    conn_escritura.commit()
    cursor_lectura.close()
    cursor_escritura.close()
    conn_lectura.close()
    conn_escritura.close()

    print(f"\n✅ ¡Listo! {total_procesados} boletines exportados a JSON, "
          f"{total_saltados} ya estaban y se omitieron.")


if __name__ == "__main__":
    main()
