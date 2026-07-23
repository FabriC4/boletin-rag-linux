package ar.gov.misiones.ccpm.boletinesrag.dto;

import jakarta.validation.constraints.NotBlank;

import java.util.List;

/**
 * Lo que manda el cliente externo. El history es opcional (para preguntas
 * de seguimiento tipo "who signed it?"), si no se manda se asume conversación nueva.
 */
public record ConsultaRequest(
        @NotBlank(message = "The question cannot be empty")
        String question,
        List<Turno> history
) {
    public record Turno(String question, String answer) {
    }

    public ConsultaRequest {
        if (history == null) {
            history = List.of();
        }
    }
}
