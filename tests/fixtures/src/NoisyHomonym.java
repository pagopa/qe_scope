package fixtures;

/**
 * Classe-rumore: ha un metodo OMONIMO di MiniService.fetchWidget ma che chiama
 * un'API diversa (noisyOp). Con la risoluzione per nome semplice, gli scenari
 * che chiamano service.fetchWidget (service: MiniService) erediterebbero anche
 * noisyOp — la risoluzione scoped per tipo del receiver deve impedirlo.
 */
public class NoisyHomonym {

    private MiniGeneratedApi api;

    public Widget fetchWidget(String id) {
        return api.noisyOp(id);
    }
}
