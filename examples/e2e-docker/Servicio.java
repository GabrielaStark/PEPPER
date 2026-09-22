// Aplicación mínima del E2E de Docker: un servicio HTTP de verdad, sin dependencias ni build system.
// Se compila con javac y se empaqueta como un jar ejecutable que imita la forma de un fat jar de
// Spring Boot (BOOT-INF/ con la configuración embebida), para que el perfil real lo clasifique y
// el núcleo lo levante igual que a un legacy de verdad.
//
// No es un legacy: es el mínimo que permite comprobar, con Docker, que PEPPER levanta un entorno
// aislado, restaura una base real, arranca la aplicación y la sirve por el ingress.

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.OutputStream;
import java.net.InetSocketAddress;

public class Servicio {

    public static void main(String[] args) throws Exception {
        int puerto = Integer.parseInt(System.getenv().getOrDefault("SERVER_PORT", "8099"));
        HttpServer servidor = HttpServer.create(new InetSocketAddress("0.0.0.0", puerto), 0);
        servidor.createContext("/", new Pagina());
        servidor.setExecutor(null);
        servidor.start();
        // El patrón de arranque que el perfil espera (`ready_log_pattern`).
        System.out.println("Started Servicio in 0.1 seconds (JVM running for 0.2)");
        System.out.flush();
    }

    static class Pagina implements HttpHandler {
        public void handle(HttpExchange intercambio) throws java.io.IOException {
            String cuerpo = "<!doctype html><html><head><title>E2E</title></head>"
                    + "<body><h1>Servicio del E2E</h1><p>ruta: " + intercambio.getRequestURI().getPath() + "</p></body></html>";
            byte[] bytes = cuerpo.getBytes("UTF-8");
            intercambio.getResponseHeaders().add("Content-Type", "text/html; charset=utf-8");
            intercambio.sendResponseHeaders(200, bytes.length);
            OutputStream salida = intercambio.getResponseBody();
            salida.write(bytes);
            salida.close();
            System.out.println("GET " + intercambio.getRequestURI().getPath() + " -> 200");
            System.out.flush();
        }
    }
}
