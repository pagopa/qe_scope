package fixtures.coverage;

// Import dal package generato: attiva lo scan contextual (deve contenere
// "generated.openapi.clients")
import it.pagopa.pn.client.b2b.generated.openapi.clients.widget.api.WidgetApi;

/**
 * Finto step file per testare lo scanner statico di coverage.py:
 *  - createWidget: chiamata su un campo *Api → match CONTEXTUAL
 *  - listWidgets:  chiamata SOLO come listWidgetsWithHttpInfo → blind spot
 *                  (lo scanner statico cerca il nome esatto dell'operationId)
 *  - deleteWidget: chiamata NON su un campo *Api → match SIMPLE (per nome)
 */
public class WidgetSteps {

    private WidgetApi widgetApi;
    private SomeService service;

    public void createWidgetStep() {
        widgetApi.createWidget("x");
    }

    public void listWidgetsStep() {
        widgetApi.listWidgetsWithHttpInfo();
    }

    public void deleteWidgetStep() {
        service.deleteWidget("id");
    }
}
