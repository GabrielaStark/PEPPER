package gob.demo.solicitudes.dao;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.Calendar;

import javax.annotation.Resource;
import javax.ejb.Stateless;
import javax.sql.DataSource;

@Stateless
public class ApplicationDao {

    @Resource(lookup = "java:jboss/datasources/SolicitudesDS")
    private DataSource dataSource;

    public String nextFolio() {
        String sql = "SELECT nextval('folio_seq')";
        try (Connection connection = dataSource.getConnection();
             PreparedStatement statement = connection.prepareStatement(sql);
             ResultSet rs = statement.executeQuery()) {
            rs.next();
            long consecutivo = rs.getLong(1);
            int anio = Calendar.getInstance().get(Calendar.YEAR);
            return String.format("SOL-%d-%06d", anio, consecutivo);
        } catch (SQLException e) {
            throw new IllegalStateException("Error generando el folio", e);
        }
    }

    public long insertApplication(String folio, long citizenId, String tipoTramite) {
        String sql = "INSERT INTO application (folio, citizen_id, tipo_tramite, estado, fecha_registro) "
                + "VALUES (?, ?, ?, 'REGISTERED', now())";
        try (Connection connection = dataSource.getConnection();
             PreparedStatement statement = connection.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            statement.setString(1, folio);
            statement.setLong(2, citizenId);
            statement.setString(3, tipoTramite);
            statement.executeUpdate();
            try (ResultSet keys = statement.getGeneratedKeys()) {
                keys.next();
                return keys.getLong(1);
            }
        } catch (SQLException e) {
            throw new IllegalStateException("Error guardando la solicitud", e);
        }
    }

    public void insertHistory(long applicationId, String estado) {
        String sql = "INSERT INTO application_history (application_id, estado, fecha) VALUES (?, ?, now())";
        try (Connection connection = dataSource.getConnection();
             PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setLong(1, applicationId);
            statement.setString(2, estado);
            statement.executeUpdate();
        } catch (SQLException e) {
            throw new IllegalStateException("Error guardando el historial de la solicitud", e);
        }
    }
}
