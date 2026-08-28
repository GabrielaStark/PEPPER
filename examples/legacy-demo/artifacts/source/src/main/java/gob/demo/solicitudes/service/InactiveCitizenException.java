package gob.demo.solicitudes.service;

import javax.ejb.ApplicationException;

@ApplicationException(rollback = true)
public class InactiveCitizenException extends RuntimeException {

    private static final long serialVersionUID = 1L;

    public InactiveCitizenException(String message) {
        super(message);
    }
}
