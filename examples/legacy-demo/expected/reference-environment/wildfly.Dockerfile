# Imagen de referencia del servidor de aplicaciones del legacy-demo.
# Reproduce el stack original: WildFly 10 sobre Java 8.
# No verificado (entorno sin Docker ni red); es especificación ejecutable.

FROM jboss/wildfly:10.1.0.Final

USER root

# Driver JDBC de PostgreSQL como módulo de JBoss.
ARG PG_DRIVER_VERSION=9.4.1212
RUN mkdir -p /opt/jboss/wildfly/modules/system/layers/base/org/postgresql/main
COPY postgresql-${PG_DRIVER_VERSION}.jar \
     /opt/jboss/wildfly/modules/system/layers/base/org/postgresql/main/
COPY module.xml \
     /opt/jboss/wildfly/modules/system/layers/base/org/postgresql/main/

# El WAR construido desde artifacts/source (mvn package).
COPY solicitudes.war /opt/jboss/wildfly/standalone/deployments/

# Datasource SolicitudesDS + nivel de log de la aplicación, aplicados antes del
# arranque (la ventaja de controlar el entorno: la observabilidad se configura
# de antemano). Equivale al fragmento rescatado en artifacts/configuration/.
COPY configure-datasource.cli /tmp/
RUN /opt/jboss/wildfly/bin/jboss-cli.sh --file=/tmp/configure-datasource.cli \
    && rm -rf /opt/jboss/wildfly/standalone/configuration/standalone_xml_history

USER jboss

CMD ["/opt/jboss/wildfly/bin/standalone.sh", "-b", "0.0.0.0"]
