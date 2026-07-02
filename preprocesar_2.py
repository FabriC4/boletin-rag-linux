import os
import json
import fitz  # PyMuPDF
import cv2
import numpy as np
import pytesseract
import psycopg2  # Conector de Postgres listo

# CONFIGURACIÓN DE TESSERACT EN WINDOWS
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Configuración de la base de datos en Docker
DB_CONFIG = {
    "dbname": "boletinDB",
    "user": "postgres",
    "password": "1234",
    "host": "127.0.0.1",
    "port": "5433",
    # Fuerza mensajes de error de Postgres en ASCII plano (evita el bug de
    # psycopg2 en Windows que rompe al decodificar errores en otro encoding)
    "options": "-c client_encoding=UTF8 -c lc_messages=C"
}

def fragmentar_texto(texto, tamaño_chunk=1000, solapamiento=200):
    """Divide el texto en pedazos más chicos con un solapamiento."""
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
    """Lee el PDF usando texto nativo o aplicando Tesseract OCR de alta estabilidad."""
    doc = fitz.open(ruta_pdf)
    nombre_archivo = os.path.basename(ruta_pdf)
    texto_completo_por_pagina = []

    print(f"📄 Procesando contenido de: {nombre_archivo}...")

    conectores_espanol = [" de ", " que ", " en ", " el ", " la ", " los ", " por ", " para "]

    for num_pagina, pagina in enumerate(doc):
        texto_nativo = pagina.get_text().strip()
        
        es_texto_valido = len(texto_nativo) > 200 and any(c in texto_nativo.lower() for c in conectores_espanol)
        
        if es_texto_valido:
            texto_final = texto_nativo
            tipo_extraccion = "nativo"
        else:
            print(f"   ⚙️ OCR Estable (Tesseract) en Pág {num_pagina + 1}...")
            
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
        
        texto_completo_por_pagina.append({
            "pagina": num_pagina + 1,
            "texto": texto_final.strip(),
            "tipo": tipo_extraccion
        })
        
    return nombre_archivo, texto_completo_por_pagina

def main():
    carpeta_boletines = "./boletines"
    textos_json = {}
    contador_chunks = 0

    if not os.path.exists(carpeta_boletines):
        print(f"❌ Error: No se encuentra la carpeta '{carpeta_boletines}'")
        return

    # Conectamos a Docker antes de empezar el bucle
    conn = None
    cursor = None
    try:
        print("🔌 Conectando a la base de datos de Docker...")
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("✅ Conectado a Postgres correctamente.")
    except UnicodeDecodeError as e:
        # Si esto pasa, Postgres mandó un error real pero en un encoding
        # que psycopg2 no puede leer. Mostramos los bytes crudos para diagnosticar.
        print(f"⚠️ Alerta: Postgres devolvió un error no legible (bytes: {e.object!r}).")
        print("   Esto suele indicar usuario/contraseña incorrectos o que la base no existe.")
        print("   Los fragmentos no tendrán nro_boletin.")
    except Exception as e:
        print(f"⚠️ Alerta: No se pudo conectar a Postgres ({e}). Los fragmentos no tendrán nro_boletin.")

    # El bucle recorre todos los archivos automáticamente
    for archivo in os.listdir(carpeta_boletines):
        if archivo.endswith(".pdf"):
            
            # --- MODIFICADO CON DIAGNÓSTICO Y BÚSQUEDA FLEXIBLE ---
            nro_boletin_real = "No mapeado"
            
            if cursor:
                try:
                    # Usamos LIKE y % para buscar el nombre del archivo al final de la ruta
                    # Ejemplo: va a coincidir tanto con "bo113550.pdf" como con "/app/boletines/bo113550.pdf"
                    busqueda = f"%{archivo}"
                    
                    cursor.execute(
                        "SELECT nro_boletin, patharchivo FROM public.boletines WHERE patharchivo LIKE %s;", 
                        (busqueda,)
                    )
                    resultado = cursor.fetchone()
                    
                    if resultado:
                        nro_boletin_real = str(resultado[0]).strip()
                        print(f"   ✅ Vinculado con éxito: {archivo} -> Boletín Nro: {nro_boletin_real}")
                    else:
                        # Esto te va a mostrar en la terminal exactamente qué archivo está fallando
                        print(f"   ❌ Sin coincidencia en la DB para el archivo: '{archivo}'")
                        
                        # [OPCIONAL] Descomentá las siguientes líneas si querés ver un ejemplo 
                        # de cómo están guardados los nombres en tu DB para corregirlo:
                        # cursor.execute("SELECT patharchivo FROM public.boletines LIMIT 1;")
                        # ejemplo = cursor.fetchone()
                        # print(f"      💡 Ejemplo de cómo figura en tu DB: '{ejemplo[0]}'")
                        
                except Exception as e:
                    print(f"   ⚠️ Error al consultar '{archivo}' en la DB: {e}")
            # -----------------------------------------------------
            ruta_completa = os.path.join(carpeta_boletines, archivo)
            nombre_pdf, paginas = extraer_contenido_pdf(ruta_completa)
            
            for p in paginas:
                if not p["texto"]:
                    continue
                    
                chunks = fragmentar_texto(p["texto"])
                
                for i, chunk in enumerate(chunks):
                    chunk_id = f"chunk_{contador_chunks}"
                    textos_json[chunk_id] = {
                        "texto": chunk,
                        "metadatos": {
                            "archivo": nombre_pdf,
                            "nro_boletin": nro_boletin_real,  # <-- MODIFICADO: Guardamos el número exacto de la DB
                            "pagina": p["pagina"],
                            "tipo_extraccion": p["tipo"],
                            "fragmento_nro": i + 1
                        }
                    }
                    contador_chunks += 1

    # Cerramos la conexión limpia (ya no hace falta .commit() porque solo lee)
    if conn and cursor:
        cursor.close()
        conn.close()
        print("🔌 Conexión con Docker cerrada correctamente.")

    with open("textos.json", "w", encoding="utf-8") as f:
        json.dump(textos_json, f, ensure_ascii=False, indent=4)
        
    print(f"\n✅ ¡Extracción finalizada con Tesseract!")
    print(f"Se generaron {contador_chunks} fragmentos en 'textos.json' mapeados con sus nro_boletin reales.")

if __name__ == "__main__":
    main()