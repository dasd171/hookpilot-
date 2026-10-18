# HookPilot hardware (design proposal — nothing built)

| Folder | Content | Status |
|---|---|---|
| `architecture/` | block diagram (Mermaid `block-diagram.md` + hand-written `block-diagram.svg`) | PROVISIONAL |
| `mechanical/` | `prototype-spec.md`: rod-holder pivot hook-set, sensor guide, retrieval drive, IP67 target | PROPOSAL rev 0 |
| `bom/` | `bom.csv` + README; all prices ESTIMATE | ESTIMATE |
| `wiring/` | `wiring-table.md` + `wiring.csv`: ESP32 + HX711 + servo + DRV8874 + INA219 + divider + e-stop chain | PROVISIONAL |

Narrative and rationale: `docs/hardware-design.md`, safety: `docs/safety.md`, calibration: `docs/calibration.md`.
No component has been purchased, assembled or bench-tested. First physical step is a bench sensor rig
without actuators (docs/roadmap.md, phase 1).
