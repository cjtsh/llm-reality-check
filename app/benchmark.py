"""Versioned selections from the published Inspect Evals datasets."""

from dataclasses import dataclass


# Bump when the samples, execution settings or aggregate formula change.
BENCHMARK_VERSION = "inspect-1"


@dataclass(frozen=True)
class Selection:
    source: str
    category: str
    ids: tuple[str | int, ...]


GPQA = (
    "recKcjTwONILZqHAX", "recvlnu8BuLmXZhSX", "reczjcMtrB1YGS2fO",
    "recGFNRVl1qBZGwyU", "recpki12iG9RUGrz9", "recReg13iV2HwJTaA",
    "rec2fsnzUuvNtUYK8", "reca44yABeO2fx7UB", "recIOlKBsOeEcgkA1",
    "rect4iLrSfUwkNTno", "recD8oX1KevFbl7bL", "recN4DY9Q5V03glmQ",
)
HUMANEVAL = tuple(f"HumanEval/{n}" for n in (29, 108, 52, 110, 54, 89, 13, 35, 39, 153, 61, 64, 132, 15, 7, 33))
BFCL_SINGLE = (
    "simple_python_70", "irrelevance_38", "parallel_45", "multiple_177",
    "irrelevance_26", "multiple_52", "simple_python_191", "simple_python_333",
)
BFCL_MULTI = (
    "multi_turn_miss_param_155", "multi_turn_long_context_110",
    "multi_turn_base_3", "multi_turn_miss_param_171", "multi_turn_long_context_88",
    "multi_turn_long_context_26", "multi_turn_miss_func_8", "multi_turn_miss_param_55",
)
AGENTBENCH = (58, 8, 79, 54, 63, 18)

PROFILES = {
    "light-v1": "Light · v1",
    "standard-v2": "Standard · v2",
    "heavy-v3": "Heavy · v3",
}
PROFILE_COUNTS = {
    "light-v1": (3, 3, 4, 0, 0),
    "standard-v2": (8, 10, 8, 4, 0),
    "heavy-v3": (12, 16, 8, 8, 6),
}


def profile_tasks(profile: str) -> tuple[Selection, ...]:
    sources = (
        ("gpqa_diamond", "reasoning", GPQA),
        ("humaneval", "coding", HUMANEVAL),
        ("bfcl_single", "tool-use", BFCL_SINGLE),
        ("bfcl_multi", "tool-use", BFCL_MULTI),
        ("agent_bench_os", "tool-use", AGENTBENCH),
    )
    return tuple(
        Selection(source, category, ids[:count])
        for (source, category, ids), count in zip(sources, PROFILE_COUNTS[profile], strict=True)
        if count
    )



def check_outcome(score: float) -> str:
    if score == 100:
        return "passed"
    if score == 0:
        return "failed"
    if 0 < score < 100:
        return "partial"
    return "ungraded"
