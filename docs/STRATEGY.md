# Product strategy

## Thesis

Loop is a contract-first autonomous execution runtime. The user delegates a goal,
authority, and budget; Loop returns artifacts plus replayable evidence. Its core
differentiator is not another chat surface or another MCP wrapper:

> A model may propose that work is finished, but only the acceptance contract and
> independently re-executed evidence can complete a strict task.

## Product shape

Loop is an application and runtime, not merely a protocol server:

- the web/desktop surfaces collect goals, contracts, authority, and approvals;
- the API persists task state, ownership, budgets, evidence, and Receipts;
- workers provide durable execution and crash recovery;
- sandboxes and gateways enforce filesystem, process, provider, and network bounds;
- MCP is one adapter family through which specialized capabilities may be exposed.

System design matters because the product promises durable handoff under failure and
least authority—not because it needs speculative internet-scale traffic.

## v0.1 release gate

A release is portfolio-ready only when all of these are reproducible:

- a fresh environment can launch the zero-key demo with one command;
- the built-in strict-contract task completes with execution verification;
- every criterion has mapped passing evidence and Receipt replay succeeds;
- CI covers the browser journey, offline unit/integration tests, dependency audits,
  desktop packaging, Redis restart/worker recovery, and Kubernetes rollback;
- README, security boundaries, architecture, and implementation agree;
- a versioned release and changelog exist;
- paid real-provider results are never fabricated or implied by mock results.

## Priority order after v0.1

1. **Evidence:** run the published suite across selected real models; report solve
   rate, false acceptances, tokens, steps, and wall time.
2. **Core reliability:** expand adversarial contracts, replay portability, crash and
   concurrency tests, and reduce orchestration complexity where it obscures invariants.
3. **Isolation:** move local Sibyl/Argus integrations behind a production-capable
   isolated MCP gateway and make browser sessions recoverable or explicitly disposable.
4. **Distribution:** sign/notarize desktop installers, publish upgrade guidance, and
   earn real external usage.
5. **Breadth:** add integrations only when they preserve the same authority, approval,
   evidence, and Receipt semantics.

## Non-goals

- Claiming that prompt injection or semantic failure is impossible.
- Matching another agent's channel or skill count before the flagship path is proven.
- Building speculative scaling layers that are not exercised by acceptance tests.
- Treating a green unit suite as proof that the documented user journey works.

## Success metrics

- verified solve rate and false-acceptance rate by suite/model/revision;
- median and tail tokens, steps, and wall time;
- Receipt replay pass rate after process restart;
- task recovery after worker loss and duplicate delivery;
- first-run demo success from a clean environment;
- external users who reproduce a Receipt rather than stars alone.
