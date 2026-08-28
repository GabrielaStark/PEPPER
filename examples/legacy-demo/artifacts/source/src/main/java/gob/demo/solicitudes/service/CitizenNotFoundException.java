package gob.demo.solicitudes.service;

import javax.ejb.ApplicationException;

@ApplicationException(rollback = true)
public class CitizenNotFoundException extends RuntimeException {

    private static final long serialVersionUID = 1L;

    public CitizenNotFoundException(String message) {
        super(message);
    }
}
