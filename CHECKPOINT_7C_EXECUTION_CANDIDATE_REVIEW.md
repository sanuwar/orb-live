# Checkpoint 7C Execution-Candidate Review

## Classification

**INERT.** The launcher verifies policy helpers and fails closed from `main()`.
It does not yet orchestrate the attended flow through an injected MCP session.
It must not be used for a real order.

## Blocking defects corrected

- Bound all account-scoped adapter reads and order payloads to one injected
  commissioned account; wrong account, symbol, side, quantity, duration, or
  market-hours posture is rejected before delegation.
- Required AAPL, exactly one whole share, regular hours, and contract-normalized
  stop pricing on every review/place payload.
- Required timezone-aware America/New_York authorizations with a positive window
  no longer than 15 minutes.
- Restricted run IDs to one safe, non-reusable path component.
- Replaced loose entry-fill scalars with an exact account/symbol/side/type/state/
  quantity predicate before the fill price can be accepted.
- Replaced cancel booleans with conservative recognized-state and exact-quantity
  reconciliation, including pending cancel and cancel-versus-fill handling.
- Refreshed reviewed source-hash pins. Ambiguous writes remain one-shot and are
  never automatically retried.

## Remaining blockers before a real order

1. Implement and review one launcher-owned orchestration path that invokes the
   adapter, consumes both authorizations at the correct points, records every
   call, and maps every ambiguous place/cancel result to `OPERATOR_REQUIRED`.
2. Implement strict account, position, and rich order-record reconciliation that
   binds the transient broker order ID to each entry/stop/exit observation.
3. Implement monotonic deadline loops and interruption recovery that can only
   resume by reconciliation or transfer control to the operator; never resubmit.
4. Review and pin the completed launcher/orchestration source, then conduct a
   separate attended go/no-go authorization. `strategy.enabled` must remain false.

## Supported live surface and intended sequence

The adapter supports only: `get_accounts`, `get_equity_positions`,
`get_equity_orders`, `review_equity_order`, `place_equity_order`, and
`cancel_equity_order`.

The intended sequence is: account/position/order preflight; entry review; one
entry place; order/position reconciliation; stop review; one stop place;
order/position reconciliation; flatten authorization; one stop cancel;
order/position reconciliation; if still long one and the stop is terminal,
exit review and one exit place; final order/position reconciliation. Reads may
repeat only within deadlines. Writes never retry, never overlap, and never
paginate. The current inert launcher does not yet execute this sequence.

## Runtime authorizations and operator actions

Two separate, non-reusable authorizations are required: the exact entry phrase
`AUTHORIZE ORB LIVE AAPL ONE SHARE ENTRY PLANNED RISK 15`, then the exact flatten
phrase `AUTHORIZE ORB LIVE AAPL ONE SHARE CONTROLLED FLATTEN`. Each binds the run
ID, current Eastern date, narrow window, AAPL, quantity 1,
`planned_risk_usd=15`, acknowledgment that actual loss may exceed planned risk,
continuous attendance, Robinhood UI availability, and no second trade.

The operator must remain present from authorization until verified flat with no
related nonterminal order. On any ambiguous write, missed deadline, inconsistent
quantity/state, unresolved stop, or interruption, automation stops at
`OPERATOR_REQUIRED`; the operator assumes emergency manual control in the
Robinhood UI. No automated exit may occur while the stop is potentially
executable, and no sell may occur when the latest position is zero.

## Evidence and validation limits

The durable atomic evidence contains only run ID, transitions, approved tool
names/counts, booleans, normalized states, conservative classifications,
durations, JSON types, record counts, reason, and `planned_risk_usd=15`. It omits
account identity, order/ref IDs, raw responses, fill/stop prices, credentials,
and tokens.

No real place response, cancel response, order reconciliation, injected-session
orchestration, provisional protection observation, or emergency-control handoff
has been live-validated. There is no live SagaExecutor binding and no normal
strategy-runner reachability.
