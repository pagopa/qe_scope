package fixtures;

/**
 * Utility statica che chiama l'API senza dot prefix (static method call).
 * Simula il pattern B2bUtils.preloadGeneric / getPreLoadResponse di pn-b2b-client:
 * il metodo è chiamato senza prefisso oggetto/classe nei metodi consumatori.
 */
public class MiniUploadUtils {

    private static MiniGeneratedApi api;

    // Chiamata diretta all'operationId: punto di arrivo della catena
    private static String resolveUpload(String key) {
        return api.initUpload(key);
    }

    // Chiamata unqualified interna: resolveUpload( senza dot
    public static String prepareUpload(String key) {
        return resolveUpload(key);
    }

    // CASO: catena lunga (>3 hop) — il fixpoint deve risolverla comunque.
    // hop4 -> hop3 -> hop2 -> prepareUpload -> resolveUpload -> api.initUpload
    private static String hop2(String key) {
        return prepareUpload(key);
    }

    private static String hop3(String key) {
        return hop2(key);
    }

    public static String deepPrepareUpload(String key) {
        return hop3(key);
    }
}
