import json
import re
import unicodedata
import urllib.request
import numpy as np
from sentence_transformers import SentenceTransformer

# ==========================================
# CONFIGURACIÓN
# ==========================================
MODELO_OLLAMA = "llama3" 

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
            numeros_encontrados = sum(1 for n in numeros_query if n in palabras_chunk)
            score_numero = (numeros_encontrados / len(numeros_query)) * 1.5

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
    
    prompt_sistema = f"""Sos un asistente experto en análisis de Boletines Oficiales.
Tu tarea es responder la pregunta del usuario utilizando ÚNICAMENTE los fragmentos de los boletines oficiales provistos en el CONTEXTO. 

Reglas estrictas:
1. Sé preciso, formal y cita textualmente si es necesario.
2. Si en el contexto no figura la respuesta o no estás seguro, decí amablemente: "No encontré información sobre ese tema en los boletines cargados". No inventes nada.

CONTEXTO DE LOS BOLETINES:
{contexto}

PREGUNTA DEL USUARIO:
{pregunta}

RESPUESTA:"""

    payload = {
        "model": MODELO_OLLAMA,
        "prompt": prompt_sistema,
        "stream": False
    }
    
    headers = {'Content-Type': 'application/json'}
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
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
    mejores_fragmentos = busqueda_hibrida(usuario_input, top_k=6)
    
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
        
        contexto_bloque += f"--- Fragmento {i+1} (Boletín Nro: {nro_boletin}) ---\n{res['texto']}\n\n"
        fuentes.append(f"📌 [Fuente] Boletín Nro: {nro_boletin} | Archivo original: {nombre_archivo} | Página: {meta['pagina']}")
        
    respuesta_ia = preguntar_a_ollama(usuario_input, contexto_bloque)
    
    print("\n🤖 Respuesta de la IA:")
    print(respuesta_ia)
    print("\n📄 Documentación de respaldo utilizada:")
    for f in fuentes:
        print(f)
    print("-" * 60 + "\n")