package ar.gov.misiones.ccpm.boletinesrag.config;

import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.impl.classic.HttpClientBuilder;
import org.apache.hc.core5.util.Timeout;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

@Configuration
public class RagClientConfig {

    private RestClient construirClient(String baseUrl, int connectTimeoutSeg, int responseTimeoutSeg) {
        var requestConfig = RequestConfig.custom()
                .setConnectTimeout(Timeout.ofSeconds(connectTimeoutSeg))
                .setResponseTimeout(Timeout.ofSeconds(responseTimeoutSeg))
                .build();

        var httpClient = HttpClientBuilder.create()
                .setDefaultRequestConfig(requestConfig)
                .build();

        var requestFactory = new HttpComponentsClientHttpRequestFactory(httpClient);

        return RestClient.builder()
                .baseUrl(baseUrl)
                .requestFactory(requestFactory)
                .build();
    }

    /**
     * Cliente hacia api_rag.py (puerto 8000) -- búsqueda + Ollama. Timeout largo:
     * el modelo puede tardar bastante en generar la respuesta.
     */
    @Bean
    @Qualifier("ragRestClient")
    public RestClient ragRestClient(
            @Value("${boletines.rag.base-url}") String baseUrl,
            @Value("${boletines.rag.timeout-seconds}") int timeoutSeconds) {
        return construirClient(baseUrl, 10, timeoutSeconds);
    }

    /**
     * Cliente hacia api_rag_bd.py (puerto 8081) -- solo consulta SQL directa,
     * sin Ollama de por medio. Timeout corto: es una query a Postgres, no
     * debería tardar más que un par de segundos.
     */
    @Bean
    @Qualifier("dbRestClient")
    public RestClient dbRestClient(
            @Value("${boletines.db.base-url}") String baseUrl,
            @Value("${boletines.db.timeout-seconds}") int timeoutSeconds) {
        return construirClient(baseUrl, 10, timeoutSeconds);
    }
}
