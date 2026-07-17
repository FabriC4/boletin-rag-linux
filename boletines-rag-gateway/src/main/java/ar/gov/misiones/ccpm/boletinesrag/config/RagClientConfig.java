package ar.gov.misiones.ccpm.boletinesrag.config;

import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.impl.classic.HttpClientBuilder;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

import java.time.Duration;

@Configuration
public class RagClientConfig {

    @Bean
    public RestClient ragRestClient(
            @Value("${boletines.rag.base-url}") String baseUrl,
            @Value("${boletines.rag.timeout-seconds}") int timeoutSeconds) {

        var requestConfig = RequestConfig.custom()
                .setConnectTimeout(Duration.ofSeconds(10))
                .setResponseTimeout(Duration.ofSeconds(timeoutSeconds)) // el modelo puede tardar
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
}
