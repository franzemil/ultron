# ultron

Voice-actuation **car manager**: says **"hey ultron"** to wake it, then a Spanish command
(*"enciende guiñador derecho"*, *"arranque"*, …) drives 9 relay channels. Runs the **same
PyTorch → TFLite pipeline** on two
candidate devices — **XIAO ESP32S3 Sense** and **Orange Pi Zero 2W** — and a benchmark picks the
winner. Successor to [`lampara-aliz`](../lampara-aliz) (Edge Impulse-based); this project trains
locally with **PyTorch as source of truth**, Edge Impulse is export-only.

## Status

🚧 **Spec phase — no implementation code yet.**

| Artifact | Where |
|---|---|
| Product plan | [.ai/sdd/PLAN.md](.ai/sdd/PLAN.md) |
| Feature spec 001 | [.ai/sdd/specs/001-ultron-car-manager/](.ai/sdd/specs/001-ultron-car-manager/) |
| Gate status | [.ai/sdd/specs/001-ultron-car-manager/.status](.ai/sdd/specs/001-ultron-car-manager/.status) |

Method: [Spec-Driven Development](https://github.com/felipefontoura/spec-driven-development-book/blob/main/BOOK.en.md).
No implementation code is written until the spec's `.status` reads `tasks:approved`.
