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

    // Método para /api/consultar
    public ConsultaResponse consultar(ConsultaRequest request) {
        return ejecutarPeticionInterna(request, "/consultar");
    }

    // Método para /api/consultabd
    public ConsultaResponse consultarBD(ConsultaRequest request) {
        // Llama al servicio de Python (mantiene /consultar si usan la misma API de FastAPI)
        return ejecutarPeticionInterna(request, "/consultar");
    }

    private ConsultaResponse ejecutarPeticionInterna(ConsultaRequest request, String pathUri) {
        List<Map<String, String>> historialMapeado = request.history().stream()
                .map(t -> Map.of("pregunta", t.question(), "respuesta", t.answer()))
                .toList();

        RagServiceRequest requestInterno = new RagServiceRequest(request.question(), historialMapeado);

        RagServiceResponse respuestaInterna;
        try {
            respuestaInterna = ragRestClient.post()
                    .uri(pathUri)
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
