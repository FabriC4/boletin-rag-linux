import duckdb

ARCHIVO_JSON = "boletines_procesados.json"

def iniciar_buscador():
    print("🔌 Conectando a DuckDB...")
    con = duckdb.connect(database=':memory:')

    print("✅ Listo. Escribe una palabra o frase para buscar en los boletines.")
    print("   (Escribe 'salir' para terminar)\n")

    while True:
        busqueda = input("🔍 Buscar texto: ").strip()
        if busqueda.lower() in ["salir", "exit", "q"]:
            break

        if not busqueda:
            continue

        # Consulta corregida usando unnest de struct directo
        query = f"""
            SELECT 
                b.archivo,
                p.pagina,
                c.fragmento_nro,
                c.texto
            FROM read_json_auto('{ARCHIVO_JSON}') AS b,
                 UNNEST(b.paginas) AS t(p),
                 UNNEST(p.chunks) AS t2(c)
            WHERE lower(c.texto) LIKE lower('%{busqueda}%')
            LIMIT 10
        """

        try:
            resultados = con.execute(query).df()

            if resultados.empty:
                print(f"❌ No se encontraron coincidencias para '{busqueda}'.\n")
            else:
                print(f"\n🎯 Se encontraron coincidencias (mostrando hasta 10):\n")
                for idx, fila in resultados.iterrows():
                    print(f"📄 Archivo: {fila['archivo']} | Pág: {fila['pagina']} | Chunk: {fila['fragmento_nro']}")
                    print(f"💬 Texto: {fila['texto'].strip()}\n" + "-"*50)

        except Exception as e:
            print(f"⚠️ Error en la consulta: {e}\n")

if __name__ == "__main__":
    iniciar_buscador()
