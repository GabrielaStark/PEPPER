package gob.demo.solicitudes.rest;

import javax.ejb.EJB;
import javax.ws.rs.Consumes;
import javax.ws.rs.POST;
import javax.ws.rs.Path;
import javax.ws.rs.Produces;
import javax.ws.rs.core.MediaType;
import javax.ws.rs.core.Response;

import gob.demo.solicitudes.model.ApplicationRequest;
import gob.demo.solicitudes.service.ApplicationService;
import gob.demo.solicitudes.service.CitizenNotFoundException;
import gob.demo.solicitudes.service.InactiveCitizenException;

@Path("/applications")
public class ApplicationsResource {

    @EJB
    private ApplicationService applicationService;

    @POST
    @Consumes(MediaType.APPLICATION_JSON)
    @Produces(MediaType.APPLICATION_JSON)
    public Response register(ApplicationRequest request) {
        try {
            String folio = applicationService.registerApplication(
                    request.getCitizenId(), request.getTipoTramite());
            return Response.status(Response.Status.CREATED)
                    .entity("{\"folio\":\"" + folio + "\"}")
                    .build();
        } catch (CitizenNotFoundException e) {
            return Response.status(Response.Status.NOT_FOUND)
                    .entity("{\"error\":\"" + e.getMessage() + "\"}")
                    .build();
        } catch (InactiveCitizenException e) {
            return Response.status(Response.Status.CONFLICT)
                    .entity("{\"error\":\"" + e.getMessage() + "\"}")
                    .build();
        }
    }
}
