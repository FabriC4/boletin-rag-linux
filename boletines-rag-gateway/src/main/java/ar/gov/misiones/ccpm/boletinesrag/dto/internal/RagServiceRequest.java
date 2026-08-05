package ar.gov.misiones.ccpm.boletinesrag.dto.internal;

import java.util.List;
import java.util.Map;

/**
 * Formato exacto que espera el servicio Python (api_rag.py / ConsultaRequest de FastAPI).
 */
public record RagServiceRequest(
        String pregunta,
        List<Map<String, String>> historial
) {
}
