# Discord Message (for #firmware-development or #general)

---

Hey everyone! I've been prototyping an alternative routing approach for Meshtastic called **System 5** — geo-clustered multi-path routing.

**TL;DR:** Instead of every node rebroadcasting, messages follow a direct path through the network. 1 TX per hop instead of N. Saves 90-99% of transmissions in most scenarios.

**Try it live (no install):** https://clemenssimon.github.io/MeshRoute/simulator.html
Click "Step" to watch flooding vs directed routing hop-by-hop on the same network.

Key points:
- Nodes self-cluster by GPS geohash, route through border nodes between clusters
- Fully backward compatible — S5 nodes work alongside legacy flooding nodes
- ESP32 firmware for Heltec V3, T-Beam, RAK4631 (standalone, no fork)
- At 1500 nodes: S5 delivers **51%** vs flooding's **36%** — it scales better

Looking for feedback on the approach and anyone interested in field testing with real hardware.

Repo: https://github.com/ClemensSimon/MeshRoute
