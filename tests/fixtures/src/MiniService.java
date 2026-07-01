package fixtures;

/**
 * Finto layer di servizio: verifica la chiusura transitiva a piu' hop.
 * fetchWidget -> loadWidget -> api.getWidget (lo step chiama fetchWidget).
 */
public class MiniService {

    private MiniGeneratedApi api;
    private MiniService helper;

    public Widget fetchWidget(String id) {
        return helper.loadWidget(id);
    }

    public Widget loadWidget(String id) {
        return api.getWidget(id);
    }
}
