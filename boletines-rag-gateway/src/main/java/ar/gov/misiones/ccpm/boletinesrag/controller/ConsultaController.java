package ar.gov.misiones.ccpm.boletinesrag.controller;

import ar.gov.misiones.ccpm.boletinesrag.dto.ConsultaRequest;
import ar.gov.misiones.ccpm.boletinesrag.dto.ConsultaResponse;
import ar.gov.misiones.ccpm.boletinesrag.service.RagQueryService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class ConsultaController {

    private final RagQueryService ragQueryService;

    public ConsultaController(RagQueryService ragQueryService) {
        this.ragQueryService = ragQueryService;
    }

    // Endpoint 1 (Existente): http://10.10.0.126:8080/api/consultar
    @PostMapping("/consultar")
    public ConsultaResponse consultar(@Valid @RequestBody ConsultaRequest request) {
        return ragQueryService.consultar(request);
    }

    // Endpoint 2 (Nuevo): http://10.10.0.126:8080/api/consultabd
    @PostMapping("/consultabd")
    public ConsultaResponse consultarBD(@Valid @RequestBody ConsultaRequest request) {
        return ragQueryService.consultarBD(request);
    }
}
