package ar.gov.misiones.ccpm.boletinesrag.dto.internal;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/** Formato exacto que devuelve api_rag_bd.py (DBSearchResponse de FastAPI). */
public record DbServiceResponse(
        String status,
        @JsonProperty("row_count") int rowCount,
        List<Fila> data
) {
    public record Fila(
            @JsonProperty("nro_boletin") int nroBoletin,
            String archivo,
            String fecha,
            String descripcion
    ) {
    }
}
