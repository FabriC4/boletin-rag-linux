package ar.gov.misiones.ccpm.boletinesrag.dto.internal;

/** Formato exacto que espera api_rag_bd.py (DBSearchRequest de FastAPI). */
public record DbServiceRequest(String query, Integer limit) {
}
