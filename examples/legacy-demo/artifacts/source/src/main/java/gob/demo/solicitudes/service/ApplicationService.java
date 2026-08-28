package gob.demo.solicitudes.service;

import java.util.logging.Logger;

import javax.ejb.EJB;
import javax.ejb.Stateless;

import gob.demo.solicitudes.dao.ApplicationDao;
import gob.demo.solicitudes.dao.CitizenDao;
import gob.demo.solicitudes.model.Citizen;

@Stateless
public class ApplicationService {

    private static final Logger LOG = Logger.getLogger(ApplicationService.class.getName());

    @EJB
    private CitizenDao citizenDao;

    @EJB
    private ApplicationDao applicationDao;

    @EJB
    private NotificationService notificationService;

    public String registerApplication(long citizenId, String tipoTramite) {
        LOG.info("Registering application for citizen " + citizenId);

        Citizen citizen = citizenDao.findById(citizenId);
        if (citizen == null) {
            LOG.warning("Citizen " + citizenId + " not found");
            throw new CitizenNotFoundException("El ciudadano " + citizenId + " no existe en el padron");
        }

        if (!"ACTIVE".equals(citizen.getStatus())) {
            LOG.warning("Citizen " + citizenId + " rejected, status: " + citizen.getStatus());
            throw new InactiveCitizenException("El ciudadano no se encuentra activo");
        }

        if (!"MX".equals(citizen.getNationality())) {
            return processForeignApplication(citizen, tipoTramite);
        }

        String folio = applicationDao.nextFolio();
        LOG.info("Folio generated: " + folio);

        long applicationId = applicationDao.insertApplication(folio, citizenId, tipoTramite);
        applicationDao.insertHistory(applicationId, "REGISTERED");

        LOG.info("Application " + folio + " registered for citizen " + citizenId);
        return folio;
    }

    private String processForeignApplication(Citizen citizen, String tipoTramite) {
        LOG.info("Processing foreign application for citizen " + citizen.getId());
        String folio = applicationDao.nextFolio();
        long applicationId = applicationDao.insertApplication(folio, citizen.getId(), tipoTramite);
        applicationDao.insertHistory(applicationId, "REGISTERED_FOREIGN");
        applicationDao.insertHistory(applicationId, "PENDING_CONSULAR_REVIEW");
        return folio;
    }
}
