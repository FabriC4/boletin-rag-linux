package ar.gov.misiones.ccpm.boletinesrag.exception;

public class RagServiceUnavailableException extends RuntimeException {
    public RagServiceUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
