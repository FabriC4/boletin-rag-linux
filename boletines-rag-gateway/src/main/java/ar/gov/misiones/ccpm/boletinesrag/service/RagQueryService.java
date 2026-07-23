package ar.gov.misiones.ccpm.boletinesrag.service;

import ar.gov.misiones.ccpm.boletinesrag.dto.ConsultaRequest;
import ar.gov.misiones.ccpm.boletinesrag.dto.ConsultaResponse;
import ar.gov.misiones.ccpm.boletinesrag.dto.internal.RagServiceRequest;
import ar.gov.misiones.ccpm.boletinesrag.dto.internal.RagServiceResponse;
import ar.gov.misiones.ccpm.boletinesrag.exception.RagServiceUnavailableException;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.List;
import java.util.Map;

/**
 * Traduce entre el contrato externo en inglés (question/answer/sources) y el
 * contrato interno en español que espera el servicio Python (pregunta/respuesta/fuentes).
 * El cambio de idioma queda contenido acá -- ni el cliente externo ni Python se enteran
 * del otro lado.
 */
@Service
public class RagQueryService {

    private final RestClient ragRestClient;

    public RagQueryService(RestClient ragRestClient) {
        this.ragRestClient = ragRestClient;
    }

    public ConsultaResponse consultar(ConsultaRequest request) {
        List<Map<String, String>> historialMapeado = request.history().stream()
                .map(t -> Map.of("pregunta", t.question(), "respuesta", t.answer()))
                .toList();

        RagServiceRequest requestInterno = new RagServiceRequest(request.question(), historialMapeado);

        RagServiceResponse respuestaInterna;
        try {
            respuestaInterna = ragRestClient.post()
                    .uri("/consultar")
                    .body(requestInterno)
                    .retrieve()
                    .body(RagServiceResponse.class);
        } catch (RestClientException e) {
            throw new RagServiceUnavailableException(
                    "Could not get a response from the bulletins service. Try again in a moment.", e);
        }

        if (respuestaInterna == null) {
            throw new RagServiceUnavailableException("The bulletins service returned an empty response.", null);
        }

        List<ConsultaResponse.Source> sources = respuestaInterna.fuentes().stream()
                .map(f -> new ConsultaResponse.Source(f.nroBoletin(), f.archivo(), f.pagina(), f.paginaFin()))
                .toList();

        return new ConsultaResponse(respuestaInterna.respuesta(), sources);
    }
}
