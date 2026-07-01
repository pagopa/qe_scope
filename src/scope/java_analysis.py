#!/usr/bin/env python3
"""
Analisi statica del sorgente Java condivisa tra coverage.py e tag-coverage.py.

UN SOLO parser, UN SOLO posto dove vivono le euristiche (e i loro bug fix).
Prima i due script avevano logiche di riconoscimento delle chiamate separate:
lo scanner statico di coverage.py era rimasto indietro rispetto al resolver di
tag-coverage (non seguiva WithHttpInfo / method reference / chiamate unqualified
/ interfaccia→impl), e questo violava l'invariante `reachable ⊆ static_covered`
facendo apparire "reale > statico" su Interop. Unificando, l'invariante torna
vera per costruzione: ogni op raggiungibile da uno scenario è un `direct` op di
qualche nodo, quindi è in `directly_invoked()`.

OpResolver: grafo su nodi (ClasseSemplice, metodo). Gli archi sono risolti col
tipo dichiarato del receiver (symbol table dei poveri: campi/variabili/parametri
del file), con espansione interfaccia→implementazioni transitiva. Fallback al
nome semplice solo per receiver non ricostruibili.
"""

import re

# Marcatori del receiver di una chiamata
_SELF = "<self>"      # chiamata unqualified / this. → stessa classe (o static import)
_BARE = None          # receiver non ricostruibile → fallback per nome semplice

_JAVA_KW = {
    "if", "while", "for", "switch", "catch", "return", "new", "assert",
    "throw", "synchronized", "super", "this", "else", "try", "finally",
    "do", "instanceof", "case", "default",
}

_METHOD_RE = re.compile(
    r'(?:@\w+(?:\([^)]*\))?\s+)*'
    r'(?:public|protected|private)\s+[\w<>,\[\]\s.?]+\s+(\w+)\s*\([^)]*\)\s*'
    r'(?:throws\s+[\w.,\s]+?)?\s*\{',
    re.DOTALL
)
# Dichiarazioni tipate (campi, variabili locali, parametri): Tipo nome ; = , )
_VAR_RE = re.compile(r'\b([A-Z]\w*)\s*(?:<[^<>;(){}]{0,120}>)?\s+(\w+)\s*[;=,)]')
_RECV_CALL_RE = re.compile(r'(\w+)\s*\.\s*(\w+)\s*\(')
_CHAIN_CALL_RE = re.compile(r'[)\]]\s*\.\s*(\w+)\s*\(')
_MREF_RE = re.compile(r'(\w+)\s*::\s*(\w+)')
_UNQ_CALL_RE = re.compile(r'(?<![.\w])([a-z]\w+)\s*\(')


def extract_body(text, start_pos):
    """Estrae il corpo delimitato da graffe a partire da start_pos."""
    depth = 1
    i = start_pos
    while i < len(text) and depth > 0:
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        i += 1
    return text[start_pos:i]


class OpResolver:
    """Risoluzione metodo → operationId con scoping per tipo del receiver.

    Nodi del grafo: (ClasseSemplice, metodo). Gli archi vengono risolti col tipo
    dichiarato del receiver (campi/variabili/parametri del file: la "symbol table
    dei poveri"), espandendo interfaccia → implementazioni. Fallback al nome
    semplice globale SOLO quando il receiver non è ricostruibile — è lì che
    sopravvivono le collisioni residue, ma il grosso (omonimi cross-classe con
    receiver tipizzato) viene eliminato.

    Espone .get(nome) come la vecchia mappa nome→ops (vista legacy: unione su
    tutte le classi) per compatibilità con test e fallback.
    """

    def __init__(self, known_ops):
        self.known_ops = known_ops
        self.methods = {}        # (cls, m) → {"direct": set, "calls": set((t, name))}
        self.by_name = {}        # m → set(cls che lo definiscono)
        self.impls_of = {}       # interfaccia/parent → set(classi figlie)
        self.parents = {}        # cls → [parent/interfacce dirette]
        self.static_imports = {} # cls → set(classi importate staticamente)
        self.ops = {}            # (cls, m) → set(ops), dopo close()

    # -- estrazione ---------------------------------------------------------

    def add_file(self, text, path=""):
        cls_m = re.search(r'\b(?:class|interface|enum|record)\s+(\w+)', text)
        if not cls_m:
            return
        cls = cls_m.group(1)

        # header di classe: extends / implements (fino alla prima graffa)
        brace = text.find('{', cls_m.end())
        header = text[cls_m.end():brace if brace != -1 else cls_m.end()]
        parents = []
        ext = re.search(r'extends\s+([\w.]+)', header)
        if ext:
            parents.append(ext.group(1).split('.')[-1])
        impl = re.search(r'implements\s+([\w.,\s]+)', header)
        if impl:
            parents += [p.strip().split('.')[-1].split('<')[0]
                        for p in impl.group(1).split(',') if p.strip()]
        self.parents.setdefault(cls, [])
        for p in parents:
            self.parents[cls].append(p)
            self.impls_of.setdefault(p, set()).add(cls)

        # static import → perimetro per le chiamate unqualified cross-file
        for si in re.findall(r'import\s+static\s+([\w.]+)\.(?:\w+|\*)\s*;', text):
            self.static_imports.setdefault(cls, set()).add(si.split('.')[-1])

        # symbol table del file: variabile → tipi dichiarati
        var_types = {}
        for vt, vn in _VAR_RE.findall(text):
            if vn not in _JAVA_KW:
                var_types.setdefault(vn, set()).add(vt)

        for m in _METHOD_RE.finditer(text):
            name = m.group(1)
            body = extract_body(text, m.end())
            node = self.methods.setdefault((cls, name), {"direct": set(), "calls": set()})
            self.by_name.setdefault(name, set()).add(cls)

            for op in self.known_ops:
                # .opId( / .opIdWithHttpInfo( / ::opId (method reference)
                if ("." + op + "(" in body or "." + op + "WithHttpInfo(" in body
                        or "::" + op in body):
                    node["direct"].add(op)

            calls = node["calls"]
            for recv, m2 in _RECV_CALL_RE.findall(body):
                if recv == "this":
                    calls.add((_SELF, m2))
                elif recv in var_types:
                    for t in var_types[recv]:
                        calls.add((t, m2))
                elif recv[0].isupper():
                    calls.add((recv, m2))       # chiamata statica Classe.metodo(
                else:
                    calls.add((_BARE, m2))      # receiver ignoto → fallback
            for m2 in _CHAIN_CALL_RE.findall(body):
                calls.add((_BARE, m2))          # catena fluida: tipo intermedio ignoto
            for recv, m2 in _MREF_RE.findall(body):
                if recv == "this":
                    calls.add((_SELF, m2))
                elif recv in var_types:
                    for t in var_types[recv]:
                        calls.add((t, m2))
                elif recv[0].isupper():
                    calls.add((recv, m2))
                else:
                    calls.add((_BARE, m2))
            for m2 in _UNQ_CALL_RE.findall(body):
                if m2 not in _JAVA_KW and m2 != name:
                    calls.add((_SELF, m2))

    # -- risoluzione --------------------------------------------------------

    def _ancestors(self, cls):
        """Catena extends/implements (BFS, con guardia sui cicli)."""
        out, queue, seen = [], list(self.parents.get(cls, [])), {cls}
        while queue:
            p = queue.pop(0)
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
            queue.extend(self.parents.get(p, []))
        return out

    def _descendants(self, cls):
        """Tutte le classi che estendono/implementano cls, transitivamente
        (es. Impl → IV3Client → IClient: l'impl è 2 livelli sotto l'interfaccia)."""
        out, queue, seen = [], list(self.impls_of.get(cls, ())), {cls}
        while queue:
            c = queue.pop(0)
            if c in seen:
                continue
            seen.add(c)
            out.append(c)
            queue.extend(self.impls_of.get(c, ()))
        return out

    def _targets(self, cls, t, name):
        """Nodi candidati per una chiamata (t, name) fatta dentro la classe cls.
        Ritorna (set di nodi, bare_fallback: bool)."""
        if t is _BARE:
            return set(), True
        if t == _SELF:
            for c in [cls] + self._ancestors(cls):
                if (c, name) in self.methods:
                    return {(c, name)}, False
            for s in self.static_imports.get(cls, ()):
                if (s, name) in self.methods:
                    return {(s, name)}, False
            # unqualified non risolto nel perimetro → fallback (recall first)
            return set(), True
        # receiver tipizzato: la classe stessa, i suoi antenati, le implementazioni
        # (discendenti transitivi: il polimorfismo vero impone l'unione)
        cand = set()
        for c in [t] + self._ancestors(t):
            if (c, name) in self.methods:
                cand.add((c, name))
        for impl in self._descendants(t):
            if (impl, name) in self.methods:
                cand.add((impl, name))
        # tipo noto ma metodo assente (es. modello generato): NIENTE fallback —
        # è qui che si eliminano le collisioni. I direct op sono già gestiti.
        return cand, False

    def close(self):
        """Chiusura transitiva a punto fisso sul grafo (classe, metodo)."""
        for node, info in self.methods.items():
            self.ops[node] = set(info["direct"])

        # pre-risoluzione archi (il grafo non cambia tra i round)
        edges = {}  # node → (set di nodi target, set di nomi bare)
        for (cls, name), info in self.methods.items():
            tgt_nodes, bare_names = set(), set()
            for (t, m2) in info["calls"]:
                nodes, bare = self._targets(cls, t, m2)
                tgt_nodes |= nodes
                if bare:
                    bare_names.add(m2)
            tgt_nodes.discard((cls, name))
            edges[(cls, name)] = (tgt_nodes, bare_names)

        for _ in range(20):  # guardia anti-loop; si stabilizza in pochi round
            changed = False
            # vista per nome (per i fallback bare), ricalcolata a ogni round
            bare_view = {}
            for (cls, name), ops in self.ops.items():
                if ops:
                    bare_view.setdefault(name, set()).update(ops)
            for node, (tgt_nodes, bare_names) in edges.items():
                acc = self.ops[node]
                before = len(acc)
                for tn in tgt_nodes:
                    acc |= self.ops.get(tn, set())
                for bn in bare_names:
                    acc |= bare_view.get(bn, set())
                if len(acc) != before:
                    changed = True
            if not changed:
                break

    # -- viste --------------------------------------------------------------

    def ops_for(self, cls, name):
        return self.ops.get((cls, name), set())

    def get(self, name, default=None):
        """Vista legacy nome→ops (unione su tutte le classi)."""
        out = set()
        for cls in self.by_name.get(name, ()):
            out |= self.ops.get((cls, name), set())
        if out:
            return out
        return default if default is not None else set()

    def directly_invoked(self):
        """Insieme di tutti gli operationId invocati DIRETTAMENTE da qualche
        parte nel sorgente (.opId( / .opIdWithHttpInfo( / ::opId).

        È la copertura STATICA: 'il metodo generato è chiamato nel codice?'.
        Garantisce reachable ⊆ directly_invoked, perché ogni op raggiungibile
        da uno scenario è un direct op del nodo terminale che lo chiama.
        """
        out = set()
        for info in self.methods.values():
            out |= info["direct"]
        return out

    def values(self):
        """Per le metriche: insiemi di ops dei nodi risolti (non vuoti)."""
        return [v for v in self.ops.values() if v]

    def __len__(self):
        return sum(1 for v in self.ops.values() if v)


def build_resolver(all_src_dirs, known_operation_ids):
    """Costruisce il resolver scoped (classe, metodo) → operationIds."""
    resolver = OpResolver(known_operation_ids)
    for d in all_src_dirs:
        if not d.exists():
            continue
        for f in d.rglob("*.java"):
            resolver.add_file(f.read_text(errors="replace"), str(f))
    resolver.close()
    return resolver
