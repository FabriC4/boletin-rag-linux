package ar.gov.misiones.ccpm.boletinesrag.dto;

import jakarta.validation.constraints.NotBlank;

import java.util.List;

/**
 * Lo que manda el cliente externo. El historial es opcional (para preguntas
 * de seguimiento tipo "¿y quién lo firmó?"), si no se manda se asume conversación nueva.
 */
public record ConsultaRequest(
        @NotBlank(message = "La pregunta no puede estar vacía")
        String pregunta,
        List<Turno> historial
) {
    public record Turno(String pregunta, String respuesta) {
    }

    public ConsultaRequest {
        if (historial == null) {
            historial = List.of();
        }
    }
}
