import json
import numpy as np
from sentence_transformers import SentenceTransformer

print("⏳ Cargando el modelo de embeddings en CPU...")
model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

def main():
    # 1. Cargamos el textos.json
    try:
        with open("textos.json", "r", encoding="utf-8") as f:
            textos_data = json.load(f)
    except FileNotFoundError:
        print("❌ Error: No se encontró 'textos.json'.")
        return

    print(f"📂 Se cargaron {len(textos_data)} fragmentos de texto.")
    
    # Guardamos los IDs en una lista ordenada para mantener el índice de la matriz
    chunk_ids = list(textos_data.keys())
    textos_a_vectorizar = [textos_data[cid]["texto"] for cid in chunk_ids]
    
    print("🧠 Generando embeddings de alta precisión (Float32)...")
    # Generamos los vectores nativos en NumPy
    embeddings = model.encode(textos_a_vectorizar, convert_to_numpy=True, show_progress_bar=True)
    
    print("💾 Guardando matriz binaria en RAM-Format...")
    # GUARDAR 1: La matriz matemática pura con todos sus decimales intactos
    np.save("vectores.npy", embeddings)
    
    # GUARDAR 2: El orden de los IDs para saber qué fila de la matriz pertenece a qué texto
    with open("mapeo_ids.json", "w", encoding="utf-8") as f:
        json.dump(chunk_ids, f)

    print(f"✅ ¡Hecho! Guardado 'vectores.npy' (matriz binaria) y 'mapeo_ids.json'.")
    print(f"Dimensiones de la matriz en RAM: {embeddings.shape}")

if __name__ == "__main__":
    main()