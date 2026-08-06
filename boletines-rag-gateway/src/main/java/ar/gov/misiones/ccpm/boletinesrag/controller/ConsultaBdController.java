package ar.gov.misiones.ccpm.boletinesrag.controller;

import ar.gov.misiones.ccpm.boletinesrag.dto.ConsultaBdRequest;
import ar.gov.misiones.ccpm.boletinesrag.dto.ConsultaBdResponse;
import ar.gov.misiones.ccpm.boletinesrag.service.DbQueryService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class ConsultaBdController {

    private final DbQueryService dbQueryService;

    public ConsultaBdController(DbQueryService dbQueryService) {
        this.dbQueryService = dbQueryService;
    }

    @PostMapping("/consultarbd")
    public ConsultaBdResponse consultarBd(@Valid @RequestBody ConsultaBdRequest request) {
        return dbQueryService.consultar(request);
    }
}
