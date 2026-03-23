# Verbesserungen aus der GitHub-Diskussion (meshtastic/firmware #9936)

## Priorität 1 — Cluster-Scoped Broadcast (KRITISCH)

**Quelle**: h3lix1 Comment #9, #11 — "98% broadcast traffic"

System 5 muss Broadcast-Traffic effizient handhaben, nicht nur Unicast.

### Umsetzung:
1. Neuer Routing-Modus in `simulator/routing.py`: `System5BroadcastRouter`
2. Intra-Cluster: Normales Flooding (klein, ~15-30 Nodes)
3. Inter-Cluster: Nur Border-Nodes relay zwischen Clustern
4. Neues Simulator-Szenario: "Broadcast Storm" — alle Nodes senden Position-Packets
5. Benchmark: TX-Savings vs. Delivery-Rate vs. Managed Flooding

### Erwartetes Ergebnis:
- ~90% weniger TX bei >95% Delivery
- Latenz steigt leicht (Relay über Border-Nodes)

---

## Priorität 2 — Bloom Filter Integration

**Quelle**: shalberd Comment #12, h3lix1 Discussion #8592

Bloom Filter an Cluster-Grenzen für Overlap-Deduplication.

### Umsetzung:
1. RBF (Reverse-Path Bloom Filter) in Broadcast-Packets an Cluster-Grenzen
2. Nodes die bereits im Filter sind, rebroadcasten nicht
3. 11-35 Bytes pro Packet — negligible overhead
4. Kombiniert System 5 Cluster-Topologie (wo) mit Bloom Filter (wer schon gesehen)

---

## Priorität 3 — Interior/Exterior Routing Dokumentation

**Quelle**: shalberd Comment #12, fifieldt Discussion #6199

System 5's Geo-Clustering IST das Interior/Exterior-Konzept von fifieldt. Das sollte explizit in der Dokumentation stehen, um die Verbindung zur Community-Diskussion herzustellen.

### Umsetzung:
- In `how-it-works.html` einen Abschnitt "Relationship to Interior/Exterior Routing" hinzufügen
- Referenz zu fifieldt's Kommentar und den Seattle Wireless Networks

---

## Priorität 4 — EU868 Congestion Awareness

**Quelle**: shalberd Comment #1, #3 — EU868 Regulierung

Shalberd betont mehrfach, dass EU868 nur einen Frequency Slot hat und besonders congestion-empfindlich ist.

### Umsetzung:
- Simulator-Parameter für EU868 vs. US-Frequenzen
- Benchmark-Szenario mit realistischen EU868-Einschränkungen
- System 5's Airtime-Savings sind hier besonders wertvoll — das in der Präsentation betonen

---

## Priorität 5 — Congestion Scaling Klarstellung

**Quelle**: shalberd Comment #1 — "not anymore" zu Congestion Scaling

Die RFC-Beschreibung erwähnt "Congestion Scaling" als Meshtastic-Feature, aber laut shalberd wurde es in v2.7.20 für ROUTER_LATE entfernt (PR #9818).

### Umsetzung:
- RFC-Text aktualisieren: "Congestion Scaling (removed for some roles in v2.7.20)"
- Zeigt dass wir die Community-Diskussion verfolgen
