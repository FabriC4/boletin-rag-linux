package ar.gov.misiones.ccpm.boletinesrag.dto;

import java.util.List;

public record ConsultaBdResponse(
        int rowCount,
        List<Result> results
) {
    public record Result(
            int bulletinNumber,
            String file
    ) {
    }
}
