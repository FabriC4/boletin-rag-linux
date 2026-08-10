package ar.gov.misiones.ccpm.boletinesrag.dto;

import java.util.List;

public record ConsultaResponse(
        String answer,
        List<Source> sources
) {
    public record Source(
            int bulletinNumber,
            String file,
            String date,
            String description
            int page,
            int pageEnd
    ) {
    }
}
