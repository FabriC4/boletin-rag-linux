package ar.gov.misiones.ccpm.boletinesrag.dto;

import java.util.List;

public record ConsultaResponse(
        String respuesta,
        List<Fuente> fuentes
) {
    public record Fuente(
            int nroBoletin,
            String archivo,
            int pagina,
            int paginaFin
    ) {
    }
}
