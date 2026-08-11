package ar.gov.misiones.ccpm.boletinesrag.service;

import ar.gov.misiones.ccpm.boletinesrag.dto.ConsultaBdRequest;
import ar.gov.misiones.ccpm.boletinesrag.dto.ConsultaBdResponse;
import ar.gov.misiones.ccpm.boletinesrag.dto.internal.DbServiceRequest;
import ar.gov.misiones.ccpm.boletinesrag.dto.internal.DbServiceResponse;
import ar.gov.misiones.ccpm.boletinesrag.exception.RagServiceUnavailableException;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.List;

/**
 * Le habla a api_rag_bd.py (puerto 8081): búsqueda full-text directa en Postgres,
 * SIN pasar por Ollama. Rápido -- solo trae qué boletines/páginas matchean.
 */
@Service
public class DbQueryService {

    private final RestClient dbRestClient;

    public DbQueryService(@Qualifier("dbRestClient") RestClient dbRestClient) {
        this.dbRestClient = dbRestClient;
    }

    public ConsultaBdResponse consultar(ConsultaBdRequest request) {
        DbServiceRequest requestInterno = new DbServiceRequest(request.query(), request.limit());

        DbServiceResponse respuestaInterna;
        try {
            respuestaInterna = dbRestClient.post()
                    .uri("/api/db-query")
                    .body(requestInterno)
                    .retrieve()
                    .body(DbServiceResponse.class);
        } catch (RestClientException e) {
            throw new RagServiceUnavailableException(
                    "Could not get a response from the database search service. Try again in a moment.", e);
        }

        if (respuestaInterna == null) {
            throw new RagServiceUnavailableException("The database search service returned an empty response.", null);
        }

        List<ConsultaBdResponse.Result> resultados = respuestaInterna.data().stream()
                .map(f -> new ConsultaBdResponse.Result(f.nroBoletin(), f.archivo(), f.fecha(), f.descripcion()))
                .toList();

        return new ConsultaBdResponse(respuestaInterna.rowCount(), resultados);
    }
}
