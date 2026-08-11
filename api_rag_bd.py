"""
Servicio interno HTTP para búsqueda directa en la base de datos de boletines
(sin pasar por Ollama -- devuelve resultados crudos de Postgres)[cite: 2].
Igual que api_rag.py: NO se expone directamente a internet[cite: 2]. Solo el backend
Java (Spring Boot) le habla, desde la misma máquina (127.0.0.1)[cite: 2]. La seguridad
(token) la maneja Java; este servicio confía en que solo le llega tráfico local[cite: 2].
Correr con:
    uvicorn api_rag_bd:app --host 127.0.0.1 --port 8081 --workers 1[cite: 2]
"""
import os
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
# ------------------------------------------------------------------
# CONFIGURACIÓN DE BASE DE DATOS
# ------------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:1234@localhost:5433/boletinDB"
)
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
app = FastAPI(
    title="Boletines - Búsqueda directa en BD",
    version="1.0.0",
    description="Servicio interno para full-text search directo en Postgres, sin pasar por Ollama."
)
class DBSearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 50
class DBSearchResponse(BaseModel):
    status: str
    row_count: int
    data: List[Dict[str, Any]]
@app.get("/health")
def health_check():
    return {"status": "ok"}
@app.post("/api/db-query", response_model=DBSearchResponse)
async def search_boletines(request: DBSearchRequest, db: Session = Depends(get_db)):
    """
    Full-text search directo sobre public.boletines (columna fts_vector),
    sin generar ninguna respuesta con Ollama -- solo trae qué boletines matchean.
    Usa phraseto_tsquery para requerir que las palabras aparezcan juntas y en
    el orden exacto ingresado (por ejemplo, "GONZALEZ FACUNDO").
    """
    try:
        sql_statement = text("""
            SELECT
                nro_boletin,
                texto_json->>'archivo' AS archivo,
                fecha,
                descripcion
            FROM public.boletines
            WHERE fts_vector @@ phraseto_tsquery('spanish', :search_term)
            LIMIT :limit;
        """)
        result = db.execute(
            sql_statement,
            {"search_term": request.query, "limit": request.limit}
        )
        columns = result.keys()
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
        return DBSearchResponse(status="success", row_count=len(rows), data=rows)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error en la consulta a la base de datos: {str(e)}"
        )
