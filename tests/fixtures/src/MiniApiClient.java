package fixtures;

/**
 * Finto wrapper layer (simula i client generati + service impl).
 * Ogni metodo incarna un caso che SCOPE deve saper risolvere.
 */
public class MiniApiClient {

    private MiniGeneratedApi api;

    // CASO: endpoint REALE — wrapper chiamato da uno step
    public Widget createWidget(String name) {
        return api.createWidget(name);
    }

    // CASO: endpoint FANTASMA — wrapper esiste ma nessuno step lo invoca
    public Widget deleteWidgetWrapper(String id) {
        return api.deleteWidget(id);
    }

    // CASO: chiamata multilinea con lambda (bug regex DOTALL della v1)
    public java.util.List<Widget> listWidgetsWrapper() {
        return performCall(
            () -> api.listWidgets());
    }

    // CASO: famiglia di versioni — wrapper per ogni versione
    public Event consumeStreamV1() {
        return api.consumeFakeStreamV1();
    }

    // CASO: clausola throws (bug removeEventStreamV24 — i metodi con throws
    // non venivano estratti dalla regex)
    public Event consumeStreamV2() throws java.lang.RuntimeException {
        return api.consumeFakeStreamV2();
    }

    public Event consumeStreamBase() {
        return api.consumeFakeStream();
    }

    // CASO: il generatore OpenAPI emette opIdWithHttpInfo accanto a opId.
    // I test possono chiamare la variante WithHttpInfo; SCOPE deve riconoscere uploadFile.
    public HttpInfo uploadFileWithHttpInfo(String name) {
        return api.uploadFileWithHttpInfo(name);
    }

    // CASO: endpoint raggiunto da step con custom parameter type
    public Widget archiveWidget(String ruolo) {
        return api.archiveWidget(ruolo);
    }

    // CASO: endpoint raggiunto via method reference (lo step usa client::cloneWidget)
    public Widget cloneWidget(String id) {
        return api.cloneWidget(id);
    }

    // CASO: endpoint raggiunto da step con annotazioni impilate
    public Widget publishWidget() {
        return api.publishWidget();
    }

    private <T> T performCall(java.util.function.Supplier<T> call) {
        return call.get();
    }
}
