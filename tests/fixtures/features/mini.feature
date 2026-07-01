@widget
Feature: Mini widget — fixture di auto-test SCOPE

  @happy
  Scenario: Creazione e lettura widget
    When viene creato un widget "test"
    Then il widget viene letto

  @happy
  Scenario: Lista widget
    When vengono elencati i widget

  @stream-v2
  Scenario: Stream con versione esplicita V2
    When viene letto lo stream con la versione "V2"
    Then vengono letti gli eventi dello stream

  @stream-any
  Scenario: Stream senza versione dichiarata
    Then vengono letti gli eventi dello stream

  @ignored
  Scenario: Scenario escluso dal runner
    When viene creato un widget "escluso"

  @upload
  Scenario: Upload file via WithHttpInfo
    When viene caricato un file "documento.pdf"
    Then viene preparato l'upload

  @custom-param
  Scenario: Archiviazione con custom parameter type
    When l'utente amministratore archivia il widget

  @stacked
  Scenario: Pubblicazione via prima annotazione impilata
    When il widget viene pubblicato

  @method-ref
  Scenario: Clonazione via method reference
    When il widget viene clonato

  @iface
  Scenario: Salvataggio via interfaccia
    When il widget viene salvato nello store

  @static-import
  Scenario: Upload profondo via static import
    When l'upload profondo viene preparato
