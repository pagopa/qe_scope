package fixtures;

/**
 * Finto dispatch a runtime per versione (il pattern del dispatch di versione a runtime):
 * la versione arriva come stringa, l'analisi statica vede tutte le versioni.
 */
public class StreamDispatcher {

    private MiniApiClient client;

    public Event readStream(String version) {
        switch (version) {
            case "V1":
                return client.consumeStreamV1();
            case "V2":
                return client.consumeStreamV2();
            default:
                return client.consumeStreamBase();
        }
    }
}
