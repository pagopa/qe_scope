package fixtures;

/**
 * Interfaccia intermedia: MiniStoreImpl implementa QUESTA, che estende IMiniStore.
 * Il glue code dichiara il campo come IMiniStore: la risoluzione deve scendere
 * transitivamente di 2 livelli (caso M2MV3EventClientImpl su Interop).
 */
public interface IMiniStoreV2 extends IMiniStore {
}
