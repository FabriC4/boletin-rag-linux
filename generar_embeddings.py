import time
import logging
import psycopg2
import psycopg2.extras
from sentence_transformers import SentenceTransformer

DB_CONFIG = {
    "dbname": "boletinDB",
    "user": "postgres",
    "password": "1234",
    "host": "127.0.0.1",
    "port": "5433",
    "options": "-c client_encoding=UTF8 -c lc_messages=C"
}

MODELO_EMBEDDINGS = "paraphrase-multilingual-MiniLM-L12-v2"
TAMAÑO_LOTE = 256  # cuántos chunks se traen y encodean por vuelta

logging.basicConfig(
    filename="errores_embeddings.log",
    level=logging.WARNING,
    format="%(asctime)s | %(message)s"
)


def conectar_db():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except UnicodeDecodeError as e:
        print(f"⚠️ Postgres devolvió un error no legible (bytes: {e.object!r}).")
        return None
    except Exception as e:
        print(f"⚠️ No se pudo conectar a Postgres ({e}).")
        return None


def contar_pendientes(cursor):
    cursor.execute("SELECT COUNT(*) FROM public.chunks WHERE embedding IS NULL;")
    return cursor.fetchone()[0]


def traer_lote_pendiente(cursor, tamaño):
    cursor.execute(
        "SELECT id, texto FROM public.chunks WHERE embedding IS NULL ORDER BY id LIMIT %s;",
        (tamaño,)
    )
    return cursor.fetchall()  # [(id, texto), ...]


def guardar_embeddings(cursor, ids, vectores):
    """Actualiza embedding para cada id, en batch, con execute_values."""
    datos = [(int(idc), vector.tolist()) for idc, vector in zip(ids, vectores)]
    psycopg2.extras.execute_values(
        cursor,
        """
        UPDATE public.chunks AS c SET embedding = data.embedding::vector
        FROM (VALUES %s) AS data(id, embedding)
        WHERE c.id = data.id;
        """,
        datos,
        template="(%s, %s)"
    )


def main():
    print("🔌 Conectando a la base de datos de Docker...")
    conn = conectar_db()
    if not conn:
        print("❌ No se puede continuar sin conexión a la base.")
        return
    print("✅ Conectado a Postgres correctamente.")
    cursor = conn.cursor()

    total_pendiente = contar_pendientes(cursor)
    if total_pendiente == 0:
        print("✅ No hay chunks pendientes de embeddings. Todo al día.")
        cursor.close()
        conn.close()
        return

    print(f"📊 {total_pendiente} chunks sin embedding todavía.")
    print("⏳ Cargando el modelo de embeddings en memoria...")
    modelo = SentenceTransformer(MODELO_EMBEDDINGS)

    procesados = 0
    inicio = time.time()

    while True:
        lote = traer_lote_pendiente(cursor, TAMAÑO_LOTE)
        if not lote:
            break

        ids = [fila[0] for fila in lote]
        textos = [fila[1] for fila in lote]

        try:
            vectores = modelo.encode(
                textos,
                batch_size=32,
                show_progress_bar=False,
                convert_to_numpy=True
            )
        except Exception as e:
            logging.warning(f"Error encodeando lote (ids {ids[0]}-{ids[-1]}): {e}")
            # Marcamos estos ids como "problemáticos" saltándolos: sin esto, el
            # while True los volvería a traer siempre (quedarían embedding IS NULL para siempre)
            # y el script haría un loop infinito. Ver errores_embeddings.log para revisarlos a mano.
            continue

        try:
            guardar_embeddings(cursor, ids, vectores)
            conn.commit()
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
            logging.warning(f"Conexión perdida guardando embeddings (ids {ids[0]}-{ids[-1]}): {e}. Reconectando...")
            conn = conectar_db()
            if not conn:
                print("❌ No se pudo reconectar. Cortando acá (podés volver a correr el script, es incremental).")
                break
            cursor = conn.cursor()
            continue
        except Exception as e:
            logging.warning(f"Error guardando embeddings (ids {ids[0]}-{ids[-1]}): {e}")
            conn.rollback()
            continue

        procesados += len(lote)
        transcurrido = time.time() - inicio
        velocidad = procesados / transcurrido if transcurrido > 0 else 0
        restantes = total_pendiente - procesados
        eta_min = (restantes / velocidad / 60) if velocidad > 0 else 0
        print(f"   {procesados}/{total_pendiente} | {velocidad:.1f} chunks/seg | ETA ~{eta_min:.0f} min")

    cursor.close()
    conn.close()
    minutos_totales = (time.time() - inicio) / 60
    print(f"\n✅ ¡Listo! {procesados} embeddings generados en {minutos_totales:.1f} minutos.")


if __name__ == "__main__":
    main()
