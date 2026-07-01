package fixtures;

import io.cucumber.java.en.Given;
import io.cucumber.java.en.Then;
import io.cucumber.java.en.When;
import static fixtures.MiniUploadUtils.deepPrepareUpload;

/**
 * Finte step definitions: una per ogni caso di parsing/risoluzione.
 */
public class MiniSteps {

    private MiniApiClient client;
    private MiniService service;
    private StreamDispatcher dispatcher;
    private IMiniStore store;

    // CASO: cucumber expression {string} + chiamata diretta al wrapper
    @When("viene creato un widget {string}")
    public void createWidgetStep(String name) {
        client.createWidget(name);
    }

    // CASO: risoluzione multi-hop (step -> service -> helper -> api)
    @Then("il widget viene letto")
    public void readWidgetStep() {
        service.fetchWidget("x");
    }

    // CASO: wrapper con lambda multilinea
    @When("vengono elencati i widget")
    public void listStep() {
        client.listWidgetsWrapper();
    }

    // CASO: dispatch per versione con parametro {string}
    @When("viene letto lo stream con la versione {string}")
    public void readStreamStep(String version) {
        dispatcher.readStream(version);
    }

    // CASO: step generico senza versione (eredita dal contesto di scenario)
    @Then("vengono letti gli eventi dello stream")
    public void readStreamGeneric() {
        dispatcher.readStream("default");
    }

    // CASO: chiamata WithHttpInfo — il generatore OpenAPI emette opId e opIdWithHttpInfo;
    // i test usano la variante WithHttpInfo, SCOPE deve comunque risolvere l'operationId base.
    @When("viene caricato un file {string}")
    public void uploadFileStep(String name) {
        client.uploadFileWithHttpInfo(name);
    }

    // CASO: custom parameter type ({ruolo} è un @ParameterType registrato in Java,
    // sconosciuto al parser) — deve matchare comunque con un pattern generico.
    @When("l'utente {ruolo} archivia il widget")
    public void archiveWidgetStep(String ruolo) {
        client.archiveWidget(ruolo);
    }

    // CASO: annotazioni step impilate — ogni alias deve diventare una step def
    // (bug revokeConsumerDelegation: solo l'ultima annotazione veniva registrata).
    @When("il widget viene pubblicato")
    @When("il widget viene pubblicato con successo")
    public void publishWidgetStep() {
        client.publishWidget();
    }

    // CASO: method reference — l'endpoint è invocato via obj::metodo
    // (bug createProducerDelegation/getStatus su Interop).
    @When("il widget viene clonato")
    public void cloneWidgetStep() {
        java.util.Optional.of("x").map(client::cloneWidget);
    }

    // CASO: catena via metodo statico senza dot prefix.
    // prepareUpload( è chiamata senza prefisso oggetto/classe (static import / stessa classe).
    @Then("viene preparato l'upload")
    public void prepareUploadStep() {
        MiniUploadUtils.prepareUpload("doc");
    }

    // CASO: interfaccia → implementazione — il campo è dichiarato col tipo
    // interfaccia (IMiniStore), l'API è chiamata da MiniStoreImpl.
    @When("il widget viene salvato nello store")
    public void storeWidgetStep() {
        store.storeWidget("x");
    }

    // CASO: static import cross-file — deepPrepareUpload è chiamata unqualified
    // ma è definita in MiniUploadUtils (import static in testa al file).
    @When("l'upload profondo viene preparato")
    public void deepUploadStep() {
        deepPrepareUpload("k");
    }
}
