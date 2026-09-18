# Forex Trading V1 Mobile — PC parity master

Mobile Cloud is now based on the tested PC v2.8.1 REGIME HOTFIX contract.

## Required parity
Command Center; full market scanner/universe; manual market lock; Market Health/regime; AI Decision Feed and reasons; Top 5/10 selection/watchlist; PAPER positions and closed trades; balance/equity/realized/unrealized P&L; risk and currency exposure; session/feed/safety state; Performance Lab; Data Quality; Neural Edge Shadow Lab/validation; settings; AI heartbeat; Start/Pause/Stop; Close All; New Session; version/update status.

## Safety invariants
- PAPER_ONLY=true
- neural_edge_shadow_only=true
- no broker-order endpoint
- PC v2.8.1 remains untouched
- server engine owns runtime state; phone is a client

## Source of truth
FX_AI_v2.8.1_REGIME_HOTFIX_PC supplied and tested by the user. The previous v2.9 synthetic mobile engine is not considered parity-complete and must not be presented as the PC engine.
