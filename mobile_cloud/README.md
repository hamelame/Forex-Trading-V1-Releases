# FX // AI v2.8.0 Mobile Cloud Beta

This cloud/mobile companion is additive: the existing Windows release assets remain untouched.

Safety invariants:
- PAPER execution only. No broker execution adapter or live-order endpoint is included.
- Neural Edge is SHADOW-only and cannot authorize, block, size, open, or close trades.
- Mobile controls affect only the isolated paper cloud session.

Render runtime: Python / Flask + Gunicorn.
