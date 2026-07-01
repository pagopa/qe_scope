package fixtures;

/**
 * Implementazione di IMiniStore: è qui che vive la chiamata all'API.
 */
public class MiniStoreImpl implements IMiniStoreV2 {

    private MiniGeneratedApi api;

    public Widget storeWidget(String id) {
        return api.storeWidget(id);
    }
}
