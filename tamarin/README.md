# ATTEST — Tamarin Formal Verification

This directory contains the Tamarin symbolic-model proofs for the **ATTEST** protocol: an accumulator-based, post-quantum verifiable-credential revocation framework for IoT.
The model formally verifies the protocol-level security properties described in Section 14 of the paper.

Tamarin version: **1.10.0**.  All 15 lemmas are verified.

---

## Files

| File | Lemma type | Count | Time |
|------|-----------|-------|------|
| `attest_baseline.spthy` | Safety (`all-traces`) | 13 | ~160 s |
| `attest_liveness.spthy` | Liveness (`exists-trace`) | 2 | ~51 s |

Two files are necessary because the liveness lemmas are non-terminating in the full model.
See [Liveness Model Design](#liveness-model-design) below.

---

## Running the Proofs

> **Never run two Tamarin instances in parallel** — precomputation is memory-intensive.

```bash
# Verify everything (~3.5 minutes total, run sequentially)
tamarin-prover attest_baseline.spthy --prove   # 160 s — 13 safety lemmas
tamarin-prover attest_liveness.spthy --prove   #  51 s — 2 liveness lemmas

# Verify a single lemma
tamarin-prover attest_baseline.spthy --prove=<lemma_name>

# Sanity check (precomputation only, no proof)
tamarin-prover attest_baseline.spthy --precompute-only
# Expected: "Refined sources: 30 cases, deconstructions complete"
```

---

## Verified Lemmas

### `attest_baseline.spthy` — Safety Lemmas

| Lemma | Steps | Time | Description |
|-------|-------|------|-------------|
| `update_gatekeeping_dlt` | 154 | ~5 s | DLT never serves updates to a revoked device (Assumption B, DLT path) |
| `update_gatekeeping_relay` | 186 | ~8 s | Honest relay never forwards updates to a revoked device (Assumption B, relay path) |
| `relay_fetch_implies_published` | 10 | ~2 s | Every relay-cached update was manager-published (helper) |
| `no_selective_omission` | 49 | ~0 s | Relay-served updates carry ΔZ and BF_Δ atomically |
| `update_revoke_mutex_dlt` | 33 | ~9 s | DLT update and revocation are mutually exclusive for the same device (helper) |
| `update_revoke_mutex_relay` | 106 | ~10 s | Relay update and revocation are mutually exclusive (helper) |
| `cumulative_revoke_mutex` | 125 | ~25 s | Cumulative relay update and revocation are mutually exclusive (helper) |
| `post_revocation_update` | 308 | ~19 s | Post-revocation epoch advance requires a ForwardUpdate (collusion) (helper) |
| `revocation_enforcement` | 2 | ~3 s | Revoked device cannot interact without prior collusion |
| `epoch_exclusion` | 2 | ~3 s | Device without post-revocation update cannot interact with current-epoch peers |
| `catch_up_correctness` | 22 | ~0 s | Cumulative update reaches the same state as sequential application |
| `update_gatekeeping` | 6 | ~2 s | Composite: conjoins DLT + relay gatekeeping (paper-facing lemma) |
| `witness_secrecy` | 4 | ~0 s | Witness components (z_i, c₁, c₂) unreachable without device compromise |

Times are pure proof times (excluding ~110 s precomputation amortised over the batch run).

### `attest_liveness.spthy` — Liveness Lemmas

| Lemma | Steps | Time | Description |
|-------|-------|------|-------------|
| `completeness_witness` | 37 | 54 s | Witness trace: two honest devices receive epoch-1 update via DLT and interact |
| `collusion_bounded_witness` | 26 | 5 s | Witness trace: compromised device forwards update to revoked device; revoked device can interact |

---

## Model Design

### Protocol Roles

Five roles are modelled using Tamarin multiset rewriting rules:

- **Manager** — issues credentials (`IssueDevice`), commits signed epoch updates to the DLT (`EpochCommit`).
- **DLT** — stores published updates (`!PublishedUpdate`); enforces the BF gate before serving update material (`DirectUpdateAllow`/`DirectUpdateDeny`).
- **Eligible Relay** — fetches updates from the DLT (`RelayFetchIfActive`); enforces the same BF gate for indirect requests (`RelayForwardAllow`/`RelayForwardDeny`); serves cumulative catch-up updates (`RelayServeCumulative`).
- **Device** — holds a credential and witness; requests updates (`DevicePrepareRequest`); applies updates (`ApplyUpdate`, `ApplyCumulativeUpdate`); compares epoch numbers before interacting (`InteractSuccess`).
- **Adversary** — standard Dolev–Yao model, extended with device compromise (`CompromiseDevice`: leaks all credential state) and explicit collusion (`ForwardUpdate`: forward a cached update to any device).

### Epoch Modeling

Epochs are modelled as **Peano naturals** using an uninterpreted unary function `s/1`:

```
'e0',  s('e0'),  s(s('e0')),  ...
```

`s(x) ≠ x` and `s(x) ≠ s(y)` when `x ≠ y` follow from Tamarin's free-algebra congruence without any arithmetic. This gives Tamarin a decidable, infinite epoch domain that precisely reflects the protocol's unbounded operation.

### State Abstractions

The model simplifies four aspects of the full protocol state without losing security content:

**Abstraction 1 — Manager state reduction.**
The full manager state `(Z, L, BF, e)` is replaced by the epoch counter `e` alone.
The only security-relevant manager action is producing correctly-signed epoch update tuples.
Update components `(acc, dz, bfd)` in `EpochCommit` are globally fresh nonces; by EUF-CMA of the signing primitive, any `!PublishedUpdate` fact in a reachable state was necessarily produced by `Init` or `EpochCommit`.

**Abstraction 2 — DLT as a persistent fact set.**
The DLT is modelled as the persistent multiset `{!PublishedUpdate(...)}`.
Tamarin's persistent facts are never consumed, directly modelling append-only ledger semantics.
BF-gated access is captured by linear `Status(tag, 'active'/'revoked')` tokens.

**Abstraction 3 — Relay BF state as shared ground truth.**
In the real protocol a relay's local BF copy may lag the DLT by at most one fetch window.
The model uses global `Status(tag, ·)` facts set immediately by `RevokeDevice` — a conservative abstraction that over-denies relative to the real protocol.
Every trace permitted by the real protocol is permitted by the model, so safety properties transfer to the real deployment.

**Abstraction 4 — Device state decomposition.**
`DeviceState(vc_i, w_i, Acc, e)` is split into `DeviceState(i, e)` (linear, epoch-tracking) plus persistent facts `!DeviceCred`, `!DeviceKey`, `!DeviceWitness`, `!DeviceTag` (device-lifetime constants).
`CompromiseDevice` leaks keys without consuming `DeviceState`, correctly modelling partial compromise.

### Loop-Breaker Elimination

Tamarin's backward proof search loops when a linear fact appears in both premiss and conclusion of the same rule ("loop-breaker").
The following choices eliminate all avoidable loops (enabling `Refined sources: 30 cases, deconstructions complete`):

| Fact | Change | Removes loops in |
|------|--------|-----------------|
| `DeviceCred`, `DeviceKey`, `DeviceWitness` | linear → `!persistent` | `DevicePrepareRequest`, `CompromiseDevice` |
| `FreshAt` | linear → `!FreshAt` | `InteractSuccess` |
| `DeviceTag` | linear → `!DeviceTag` | `RevokeDevice`, relay and DLT update rules |
| `RelayState` | eliminated | `RelayFetchIfActive` (replaced by `!HonestRelay`) |
| `CurrentUpdate` reads | 3 looping reads → `!PublishedUpdate` reads | `RegisterRelay`, `IssueDevice`, `DevicePrepareRequest` |

Two loops are **necessary** and remain:
- `DeviceState(i, e)`: reproduced by `DevicePrepareRequest`; advanced by `ApplyUpdate`/`ApplyCumulativeUpdate`. Models the device epoch lifecycle. Handled by `[use_induction]` on the affected lemmas and by `heuristic: I`.
- `Status(tagR, 'active')` in relay rules: mutable token (irreversibly transitioned by `RevokeDevice`). Handled by `heuristic: I`.

---

## Liveness Model Design

### Why a Separate File

The `completeness` and `collusion_bounded` lemmas are **non-terminating** in `attest_baseline.spthy`.

**Root cause (three layers):**
1. Six rules produce `DeviceState`: `IssueDevice`, `DevicePrepareRequest` (loop-breaker), `ApplyUpdate`, `ApplyCumulativeUpdate`, `ForwardUpdate` (loop-breaker), `InteractSuccess` (loop-breakers for both devices). Tamarin branches on all six.
2. The `s/1` successor function creates an infinite epoch domain. Each new epoch enables a new `DevicePrepareRequest` iteration, making the backward search depth unbounded.
3. `heuristic: I` resolves loop-breakers eagerly, so exponential branching hits immediately rather than being deferred.

### Fixes Applied in `attest_liveness.spthy`

| Change | Effect |
|--------|--------|
| Concrete epochs `'e0'`, `'e1'` (no `s/1`) | Eliminates infinite epoch domain; `EpochCommit` becomes a single deterministic step |
| One-shot `InteractSuccess` (DeviceState consumed) | Removes `InteractSuccess` as a DeviceState loop-breaker |
| `UniqueUpdateRequest` restriction | Bounds `DevicePrepareRequest` to at most one per `(device, epoch)` pair |
| **`!HonestDevice(to)` in `ForwardUpdate`** (key insight) | Removes `ForwardUpdate` as a DeviceState loop-breaker; see below |
| `UniqueForwardTo` restriction | Bounds `ForwardUpdate` to at most one per `(target, epoch)` pair |
| Per-lemma `heuristic: s` | Oracle heuristic guides exists-trace search over the bounded concrete-epoch model |

### The `!HonestDevice` Fix

In the baseline, `ForwardUpdate` reads and **reproduces** `DeviceState(to, eT)`, making it a loop-breaker.
For `completeness_witness` — which has no `ForwardUpdate` action in its formula — Tamarin still branches on `ForwardUpdate` as a potential `DeviceState` source, causing exponential blowup.

**Fix:** replace `DeviceState(to, 'e0')` in `ForwardUpdate`'s premiss with `!HonestDevice(to)` (a persistent fact produced by `IssueDevice`).
- `ForwardUpdate` no longer reads or writes `DeviceState` → **not a loop-breaker**.
- `DeviceState(to, 'e0')` is left untouched and remains available for `ApplyUpdate` to consume.
- This also enables both liveness lemmas to coexist in one file (previously impossible) and reduces `collusion_bounded_witness` from 501 s to 5 s.

**Soundness:** `!HonestDevice(to)` implies `to` was issued at `'e0'`, which is exactly when `DeviceState(to, 'e0')` exists in the full model. The witness traces produced by `attest_liveness.spthy` are valid execution traces of `attest_baseline.spthy` under the same rule applications with `'e1' := s('e0')`.

### Why Single-File Combination Is Not Possible

Combining both models into one file requires concrete-epoch constants in the **rules**, not just in the lemma formulas.
Using `s('e0')` in a liveness lemma formula while the rules still contain `s(e)` (with variable `e`) leaves the backward search unconstrained: Tamarin can set `e = 'e0'`, `e = s('e0')`, `e = s(s('e0'))`, etc.
Making the rules concrete requires replacing the entire `attest_baseline.spthy` rule set with concrete-epoch versions — which is exactly what `attest_liveness.spthy` already is.
Embedding both rule sets in one theory roughly doubles the precomputation case count and risks OOM.
