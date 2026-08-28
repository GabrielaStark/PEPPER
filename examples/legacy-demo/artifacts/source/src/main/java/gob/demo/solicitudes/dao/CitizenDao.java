package gob.demo.solicitudes.dao;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;

import javax.annotation.Resource;
import javax.ejb.Stateless;
import javax.sql.DataSource;

import gob.demo.solicitudes.model.Citizen;

@Stateless
public class CitizenDao {

    @Resource(lookup = "java:jboss/datasources/SolicitudesDS")
    private DataSource dataSource;

    public Citizen findById(long citizenId) {
        String sql = "SELECT id, nombre, status, nationality FROM citizen WHERE id = ?";
        try (Connection connection = dataSource.getConnection();
             PreparedStatement statement = connection.prepareStatement(sql)) {
            statement.setLong(1, citizenId);
            try (ResultSet rs = statement.executeQuery()) {
                if (!rs.next()) {
                    return null;
                }
                Citizen citizen = new Citizen();
                citizen.setId(rs.getLong("id"));
                citizen.setNombre(rs.getString("nombre"));
                citizen.setStatus(rs.getString("status"));
                citizen.setNationality(rs.getString("nationality"));
                return citizen;
            }
        } catch (SQLException e) {
            throw new IllegalStateException("Error consultando el padron de ciudadanos", e);
        }
    }
}
