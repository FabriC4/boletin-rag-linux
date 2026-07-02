import json
import re
import unicodedata
import urllib.request
import numpy as np
from sentence_transformers import SentenceTransformer

# ==========================================
# CONFIGURACIÓN
# ==========================================
MODELO_OLLAMA = "gemma2:2b" 

print("🧠 Cargando base de conocimiento en la memoria RAM...")
with open("textos.json", "r", encoding="utf-8") as f:
    textos_db = json.load(f)

with open("mapeo_ids.json", "r", encoding="utf-8") as f:
    mapeo_ids = json.load(f)

vectores_db = np.load("vectores.npy")

print("⏳ Iniciando el modelo de embeddings en RAM...")
model_embeddings = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

def normalizar_texto(texto):
    """Limpia el texto quitando tildes, mayúsculas, guiones y signos de puntuación."""
    texto = texto.lower()
    texto = ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')
    texto = re.sub(r'[-_.,;:¡!¿?()"\']', ' ', texto)
    return ' '.join(texto.split())

def busqueda_hibrida(query, top_k=3):
    """Busca en la RAM combinando vectores (semántica) y texto normalizado (literal)."""
    query_limpia = normalizar_texto(query)
    palabras_query = query_limpia.split()

    # Números exactos en la consulta (ej: "512" de "decreto 512").
    # Son identificadores precisos: si el chunk los tiene como palabra exacta,
    # es una señal mucho más fuerte que cualquier similitud semántica genérica.
    numeros_query = [p for p in palabras_query if p.isdigit()]

    query_vector = model_embeddings.encode(query, convert_to_numpy=True)
    
    norm_vectores = np.linalg.norm(vectores_db, axis=1)
    norm_query = np.linalg.norm(query_vector)
    norm_vectores[norm_vectores == 0] = 1e-9 
    
    similitudes_vectoriales = np.dot(vectores_db, query_vector) / (norm_vectores * norm_query)
    
    resultados = []
    for idx, chunk_id in enumerate(mapeo_ids):
        score_semantico = float(similitudes_vectoriales[idx])
        texto_original = textos_db[chunk_id]["texto"]
        texto_chunk_limpio = normalizar_texto(texto_original)
        palabras_chunk = set(texto_chunk_limpio.split())
        
        score_literal = 0.0
        if query_limpia in texto_chunk_limpio:
            score_literal += 0.6
        elif palabras_query:
            coincidencias = sum(1 for palabra in palabras_query if palabra in texto_chunk_limpio)
            score_literal += (coincidencias / len(palabras_query)) * 0.3

        # Boost fuerte por número exacto (palabra completa, no substring) presente en el chunk
        score_numero = 0.0

        if numeros_query:
            numeros_encontrados = [n for n in numeros_query if n in palabras_chunk]
            
            if not numeros_encontrados:
                continue

            score_numero = (len(numeros_encontrados) / len(numeros_query)) * 2.0

        score_final = score_semantico + score_literal + score_numero
        
        resultados.append({
            "chunk_id": chunk_id,
            "score": score_final,
            "texto": texto_original,
            "metadatos": textos_db[chunk_id]["metadatos"]
        })
        
    resultados.sort(key=lambda x: x["score"], reverse=True)
    return resultados[:top_k]

def preguntar_a_ollama(pregunta, contexto):
    """Envía el contexto estructurado y la pregunta a la API local de Ollama."""
    url = "http://localhost:11434/api/generate"

    prompt_sistema = f"""
Sos un asistente experto en análisis de Boletines Oficiales.

Tu tarea es responder utilizando ÚNICAMENTE la información contenida en los fragmentos del contexto.

Reglas:
1. Respondé de forma clara, precisa y formal.

2. Si la pregunta pide identificar en qué boletines aparece una persona, nombre, decreto, ley o término:
   - SOLO debés responder usando el campo "Boletín:" del contexto.
   - Nunca uses números encontrados dentro del contenido como número de boletín.
   - El número de boletín es únicamente el que aparece después de "Boletín:".
   - No digas fragmentos.

3. Si la pregunta pide explicar, resumir o detallar contenido:
   - Respondé normalmente usando la información encontrada.

4. Si no encontrás información:
   - Respondé exactamente:
   "No encontré información sobre ese tema en los boletines cargados."

5. Nunca inventes datos.

CONTEXTO:
{contexto}

PREGUNTA:
{pregunta}

RESPUESTA:
"""

    payload = {
        "model": MODELO_OLLAMA,
        "prompt": prompt_sistema,
        "stream": False
    }

    headers = {'Content-Type': 'application/json'}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            res_json = json.loads(response.read().decode('utf-8'))
            return res_json.get("response", "")
    except Exception as e:
        return f"\n❌ Error al conectar con Ollama ({e})."

# ==========================================
# BUCLE PRINCIPAL DEL CHAT
# ==========================================
print("\n🚀 ¡Buscador Híbrido Optimizado Listo!")
print("Para salir, escribí 'salir'.\n")

while True:
    usuario_input = input("👤 Tu pregunta: ")
    if usuario_input.strip().lower() == "salir":
        print("¡Nos vemos!")
        break
        
    if not usuario_input.strip():
        continue
        
    print("🔍 Buscando...")
    mejores_fragmentos = busqueda_hibrida(usuario_input, top_k=4)

    print("\n🧪 DEBUG - Fragmentos recuperados:")
    for r in mejores_fragmentos:
        print(f"   score={r['score']:.3f} | boletin={r['metadatos'].get('nro_boletin')} | archivo={r['metadatos']['archivo']} | preview={r['texto'][:60]!r}")
    print()
    
    contexto_bloque = ""
    fuentes = []
    
    for i, res in enumerate(mejores_fragmentos):
        meta = res["metadatos"]
        nombre_archivo = meta['archivo']
        
        # --- MODIFICADO: El número de boletín ya viene guardado directamente en el JSON ---
        nro_boletin = meta.get('nro_boletin', 'No mapeado')
        
        contexto_bloque += f""" --- Fragmento {i+1} --- Boletín: {nro_boletin} Página: {meta['pagina']} Archivo: {nombre_archivo}
        Contenido:
        {res['texto']}

"""
        fuentes.append(f"📌 [Fuente] Boletín Nro: {nro_boletin} | Archivo original: {nombre_archivo} | Página: {meta['pagina']}")
        
    respuesta_ia = preguntar_a_ollama(usuario_input, contexto_bloque)
    
    print("\n🤖 Respuesta de la IA:")
    print(respuesta_ia)
    print("\n📄 Documentación de respaldo utilizada:")
    for f in fuentes:
        print(f)
    print("-" * 60 + "\n")