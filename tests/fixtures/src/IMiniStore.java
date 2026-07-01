package fixtures;

/**
 * Interfaccia di servizio: il glue code dichiara il campo col tipo interfaccia,
 * l'implementazione che chiama l'API è MiniStoreImpl. La risoluzione scoped
 * deve espandere interfaccia → implementazioni.
 */
public interface IMiniStore {
    Widget storeWidget(String id);
}
