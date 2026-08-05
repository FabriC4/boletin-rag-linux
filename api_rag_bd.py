import os
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

# ------------------------------------------------------------------
# 1. AUTENTICACIÓN Y SEGURIDAD
# ------------------------------------------------------------------
API_BEARER_TOKEN = os.getenv("API_BEARER_TOKEN", "ccpm2026")
security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Verifica que el Bearer Token enviado en el Header sea válido."""
    if credentials.credentials != API_BEARER_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Token de autorización inválido o ausente."
        )
    return credentials.credentials

# ------------------------------------------------------------------
# 2. CONFIGURACIÓN DE BASE DE DATOS
# ------------------------------------------------------------------
# Cadena ajustada: postgresql://postgres:1234@localhost:5432/boletines_ubuntu
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

# ------------------------------------------------------------------
# 3. INICIALIZACIÓN DE FASTAPI Y ESQUEMAS
# ------------------------------------------------------------------
app = FastAPI(
    title="Boletines RAG & DB API",
    version="1.0.0",
    description="API para búsqueda automatizada en boletines mediante PostgreSQL FTS y RAG."
)

class DBSearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 50

class DBSearchResponse(BaseModel):
    status: str
    row_count: int
    data: List[Dict[str, Any]]

class RAGQueryRequest(BaseModel):
    prompt: str
    top_k: Optional[int] = 3

class RAGQueryResponse(BaseModel):
    answer: str
    context_documents: List[str]

# ------------------------------------------------------------------
# 4. ENDPOINTS
# ------------------------------------------------------------------

@app.get("/health")
def health_check():
    """Endpoint público de verificación de estado."""
    return {"status": "ok"}


@app.post("/api/db-query", response_model=DBSearchResponse)
async def search_boletines(
    request: DBSearchRequest, 
    db: Session = Depends(get_db),
    token: str = Depends(verify_token)
):
    """
    Ejecuta una consulta FTS sobre 'public.boletines' usando la base de datos 'boletines_ubuntu'.
    """
    try:
        sql_statement = text("""
            SELECT 
                nro_boletin, 
                texto_json->>'archivo' AS archivo
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

        return DBSearchResponse(
            status="success",
            row_count=len(rows),
            data=rows
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500, 
            detail=f"Error en la consulta a la base de datos: {str(e)}"
        )


@app.post("/api/rag/query", response_model=RAGQueryResponse)
async def query_rag(
    request: RAGQueryRequest,
    token: str = Depends(verify_token)
):
    """
    Endpoint del pipeline RAG.
    """
    try:
        retrieved_docs = ["Ejemplo de contexto recuperado 1", "Ejemplo de contexto recuperado 2"]
        generated_answer = f"Respuesta generada para: '{request.prompt}'"
        
        return RAGQueryResponse(
            answer=generated_answer,
            context_documents=retrieved_docs
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Error procesando RAG: {str(e)}"
        )

# ------------------------------------------------------------------
# 5. EJECUCIÓN DEL SERVIDOR
# ------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_rag_bd:app", host="0.0.0.0", port=8080, reload=True)
