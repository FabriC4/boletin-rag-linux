package ar.gov.misiones.ccpm.boletinesrag.dto;

import jakarta.validation.constraints.NotBlank;

public record ConsultaBdRequest(
        @NotBlank(message = "The query cannot be empty")
        String query,
        Integer limit
) {
    public ConsultaBdRequest {
        if (limit == null || limit <= 0) {
            limit = 50;
        }
    }
}
