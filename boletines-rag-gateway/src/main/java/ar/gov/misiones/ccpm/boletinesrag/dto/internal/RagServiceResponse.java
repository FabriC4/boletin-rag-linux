package ar.gov.misiones.ccpm.boletinesrag.dto.internal;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * Formato exacto que devuelve el servicio Python (api_rag.py / ConsultaResponse de FastAPI).
 * Usa snake_case porque así lo genera Pydantic; acá lo mapeamos a los nombres que Jackson
 * necesita en Java con @JsonProperty.
 */
public record RagServiceResponse(
        String respuesta,
        List<Fuente> fuentes
) {
    public record Fuente(
            @JsonProperty("nro_boletin") int nroBoletin,
            String archivo,
            int pagina,
            @JsonProperty("pagina_fin") int paginaFin
    ) {
    }
}
