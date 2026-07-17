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

@Service
public class RagQueryService {

    private final RestClient ragRestClient;

    public RagQueryService(RestClient ragRestClient) {
        this.ragRestClient = ragRestClient;
    }

    public ConsultaResponse consultar(ConsultaRequest request) {
        List<Map<String, String>> historialMapeado = request.historial().stream()
                .map(t -> Map.of("pregunta", t.pregunta(), "respuesta", t.respuesta()))
                .toList();

        RagServiceRequest requestInterno = new RagServiceRequest(request.pregunta(), historialMapeado);

        RagServiceResponse respuestaInterna;
        try {
            respuestaInterna = ragRestClient.post()
                    .uri("/consultar")
                    .body(requestInterno)
                    .retrieve()
                    .body(RagServiceResponse.class);
        } catch (RestClientException e) {
            throw new RagServiceUnavailableException(
                    "No se pudo obtener respuesta del servicio de boletines. Probá de nuevo en un momento.", e);
        }

        if (respuestaInterna == null) {
            throw new RagServiceUnavailableException("El servicio de boletines devolvió una respuesta vacía.", null);
        }

        List<ConsultaResponse.Fuente> fuentes = respuestaInterna.fuentes().stream()
                .map(f -> new ConsultaResponse.Fuente(f.nroBoletin(), f.archivo(), f.pagina(), f.paginaFin()))
                .toList();

        return new ConsultaResponse(respuestaInterna.respuesta(), fuentes);
    }
}
