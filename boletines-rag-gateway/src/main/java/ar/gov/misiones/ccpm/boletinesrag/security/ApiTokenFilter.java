package ar.gov.misiones.ccpm.boletinesrag.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * Valida el header "Authorization: Bearer <token>" en cada pedido a /api/**.
 * Si falta o no coincide, corta acá con 401 y el controller nunca se entera del pedido.
 *
 * La comparación usa MessageDigest.isEqual (tiempo constante) en vez de String.equals,
 * para no filtrar por timing cuánto del token coincide -- práctica estándar al comparar secretos.
 */
@Component
public class ApiTokenFilter extends OncePerRequestFilter {

    private final String tokenEsperado;

    public ApiTokenFilter(@Value("${boletines.api.token}") String tokenEsperado) {
        this.tokenEsperado = tokenEsperado;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        // Solo protegemos las rutas de la API; dejamos pasar libre cosas como /actuator/health si las agregás después
        return !request.getRequestURI().startsWith("/api/");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {

        String header = request.getHeader("Authorization");
        String tokenRecibido = (header != null && header.startsWith("Bearer "))
                ? header.substring("Bearer ".length())
                : null;

        if (tokenRecibido == null || !tokensIguales(tokenRecibido, tokenEsperado)) {
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            response.getWriter().write("""
                    {"error": "Invalid or missing token. Send the header Authorization: Bearer <token>."}
                    """);
            return;
        }

        chain.doFilter(request, response);
    }

    private boolean tokensIguales(String a, String b) {
        byte[] ba = a.getBytes(StandardCharsets.UTF_8);
        byte[] bb = b.getBytes(StandardCharsets.UTF_8);
        return MessageDigest.isEqual(ba, bb);
    }
}
