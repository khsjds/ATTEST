"""
ATTEST parameter sets.

Two configurations are provided, both inherited from COMPASS (INDOCRYPT 2025):

┌──────────────────┬──────────────────────────┬──────────────────────────┐
│                  │  SET1                    │  SET2  (recommended)     │
├──────────────────┼──────────────────────────┼──────────────────────────┤
│ Ring dim   N     │  512                     │  1024                    │
│ Modulus    q     │  205,207,553  (~2^27.6)  │  4,294,957,057 (~2^32)  │
│ Hash param κ     │  44                      │  36                      │
│ Gaussian   σ     │  11,336                  │  167,771                 │
│ PF dim     t     │  256                     │  512                     │
├──────────────────┼──────────────────────────┼──────────────────────────┤
│ Witness size     │  4.2 KB                  │  8.2 KB                  │
│ ΔZ wire size     │  2.0 KB                  │  4.0 KB                  │
│ BF_Δ (K=10K,1%) │  11.7 KB                 │  11.7 KB  (BF is same)  │
│ Add epoch bcast  │  ~2 KB                   │  ~4 KB                   │
│ Del epoch bcast  │  ~14 KB                  │  ~16 KB                  │
├──────────────────┼──────────────────────────┼──────────────────────────┤
│ Verify (Colab)   │  ~8 ms (est.)            │  ~80 ms                  │
│ Verify (Mac M)   │  ~1 ms                   │  ~12 ms                  │
│ WitUpdate        │  ~0.4 ms                 │  ~0.7 ms                 │
│ BatchAdd/member  │  slow (tight rejection)  │  ~12 ms (Mac) / ~68 ms  │
├──────────────────┼──────────────────────────┼──────────────────────────┤
│ Use when         │  Quick experiments,      │  Paper numbers, IoT      │
│                  │  CI/CD, Colab demos      │  deployment evaluation   │
└──────────────────┴──────────────────────────┴──────────────────────────┘

Note: SET1 accumulation is paradoxically slower than SET2 on some platforms
because its tighter Gaussian parameters (σ=11,336) cause more rejection
sampling iterations.  SET2 (σ=167,771) is the recommended production setting
and produces the numbers cited in the ATTEST paper.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ParamSet:
    """Immutable descriptor for one ATTEST/COMPASS parameter set."""
    name        : str
    N           : int    # ring dimension
    q           : int    # ring modulus
    kappa       : int    # hash parameter κ
    sigma       : float  # Gaussian std σ
    t           : int    # partial Fourier dimension
    witness_kb  : float  # approx. witness size in KB
    delta_z_kb  : float  # approx. ΔZ wire size in KB
    verify_ms_colab : float   # approx. verify time on Google Colab (ms)

    def __str__(self):
        return (f"ATTEST {self.name}: N={self.N}, q={self.q}, "
                f"κ={self.kappa}, σ={self.sigma}, t={self.t}")


SET1 = ParamSet(
    name           = "set1",
    N              = 512,
    q              = 205_207_553,
    kappa          = 44,
    sigma          = 11_336,
    t              = 256,
    witness_kb     = 4.2,
    delta_z_kb     = 2.0,
    verify_ms_colab = 8.0,   # estimated
)

SET2 = ParamSet(
    name           = "set2",
    N              = 1024,
    q              = 4_294_957_057,
    kappa          = 36,
    sigma          = 167_771,
    t              = 512,
    witness_kb     = 8.2,
    delta_z_kb     = 4.0,
    verify_ms_colab = 80.0,  # measured in COMPASS paper (Google Colab)
)

PARAM_SETS = {"set1": SET1, "set2": SET2}

# Library-wide defaults (used by manager_setup when no param_set is given)
DEFAULT_PARAM_SET = "set2"
K_MAX             = 10_000   # default max credential capacity
BF_FP_RATE        = 0.01     # 1% false-positive rate
LAMBDA_SEC        = 128      # security parameter λ

# Aliases kept for backwards compatibility
PARAM_SET = DEFAULT_PARAM_SET
