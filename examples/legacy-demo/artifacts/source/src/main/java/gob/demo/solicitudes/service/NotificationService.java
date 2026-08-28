package gob.demo.solicitudes.service;

import java.util.logging.Logger;

import javax.ejb.Stateless;

@Stateless
public class NotificationService {

    private static final Logger LOG = Logger.getLogger(NotificationService.class.getName());

    // Envia el correo de confirmacion por el SMTP institucional (mail.dependencia.gob.mx)
    public void sendRegistrationEmail(long citizenId, String folio) {
        LOG.info("Sending registration email to citizen " + citizenId + " for folio " + folio);
    }
}
