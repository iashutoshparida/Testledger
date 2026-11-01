"""Reproducible evaluation of the flaky detector against a simulated CI history with known truth.

Ground truth per test:
* flaky       fails at random with a fixed probability on every run (including re-runs);
* regression  starts failing at one commit and keeps failing (some are fixed later);
* stable      always passes.
Some commits are re-run, as CI retries do. Every run goes through the real ingest path
(JUnit XML -> parser -> database -> quarantine policy), so this measures the system, not
just the scoring function.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from testledger.config import Settings
from testledger.db import make_engine, make_session_factory
from testledger.models import Base
from testledger.service import LedgerService


@dataclass(frozen=True)
class Scenario:
    seed: int = 7
    commits: int = 150
    stable_tests: int = 250
    flaky_probs: tuple[float, ...] = (0.05, 0.05, 0.1, 0.1, 0.1, 0.15, 0.2, 0.2, 0.3, 0.4)
    regressions: int = 8
    rerun_rate: float = 0.15


@dataclass
class Metrics:
    true_flaky: int
    flagged: int
    true_positives: int
    false_positives: list[str]
    missed: list[str]
    regressions_flagged: int
    median_runs_to_detect: float | None
    naive_flagged: int
    naive_false_positives: int
    runs_ingested: int
    detections: dict[str, int] = field(default_factory=dict)

    @property
    def precision(self) -> float:
        return self.true_positives / self.flagged if self.flagged else 1.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.true_flaky if self.true_flaky else 1.0


def _xml(outcomes: dict[str, bool]) -> bytes:
    cases = "".join(
        f'<testcase classname="sim" name="{name}" time="0.01">'
        f"{'' if ok else '<failure message=\"sim\"/>'}</testcase>"
        for name, ok in outcomes.items()
    )
    return f"<testsuite>{cases}</testsuite>".encode()


def run(scenario: Scenario, settings: Settings | None = None) -> Metrics:
    rng = random.Random(scenario.seed)  # noqa: S311 - simulation, not cryptography
    settings = settings or Settings(api_key_hashes="")
    flaky = {f"flaky_{i:02d}": p for i, p in enumerate(scenario.flaky_probs)}
    regressions: dict[str, tuple[int, int]] = {}
    for i in range(scenario.regressions):
        start = rng.randrange(10, scenario.commits - 10)
        # half are fixed after a few commits, half stay broken
        end = start + rng.randrange(3, 15) if i % 2 == 0 else scenario.commits + 1
        regressions[f"regress_{i:02d}"] = (start, end)
    stable = [f"stable_{i:03d}" for i in range(scenario.stable_tests)]

    with TemporaryDirectory() as tmp:
        engine = make_engine(f"sqlite:///{Path(tmp) / 'eval.db'}")
        Base.metadata.create_all(engine)
        service = LedgerService(make_session_factory(engine), settings)
        detections: dict[str, int] = {}
        fail_counts: dict[str, int] = {}
        run_no = 0
        for commit in range(scenario.commits):
            attempts = 2 if rng.random() < scenario.rerun_rate else 1
            for attempt in range(attempts):
                run_no += 1
                outcomes = {name: True for name in stable}
                for name, p in flaky.items():
                    outcomes[name] = rng.random() >= p
                for name, (start, end) in regressions.items():
                    outcomes[name] = not (start <= commit < end)
                for name, ok in outcomes.items():
                    if not ok:
                        fail_counts[name] = fail_counts.get(name, 0) + 1
                result = service.ingest(
                    "sim", f"c{commit}-a{attempt}", f"{commit:08x}", "main", _xml(outcomes)
                )
                for test_id in result.quarantined:
                    detections.setdefault(test_id.removeprefix("sim::"), run_no)
        engine.dispose()

    flagged = set(detections)
    truth = set(flaky)
    delays = [detections[n] for n in flagged & truth]
    naive = {name for name, count in fail_counts.items() if count >= 2}
    return Metrics(
        true_flaky=len(truth),
        flagged=len(flagged),
        true_positives=len(flagged & truth),
        false_positives=sorted(flagged - truth),
        missed=sorted(truth - flagged),
        regressions_flagged=len(flagged & set(regressions)),
        median_runs_to_detect=median(delays) if delays else None,
        naive_flagged=len(naive),
        naive_false_positives=len(naive - truth),
        runs_ingested=run_no,
        detections={k: detections[k] for k in sorted(detections)},
    )


@dataclass
class Aggregate:
    seeds: int
    runs_ingested: int
    true_flaky: int
    true_positives: int
    flagged: int
    regressions: int
    regressions_flagged: int
    naive_flagged: int
    naive_false_positives: int
    median_runs_to_detect: float
    missed_by_prob: dict[float, int]

    @property
    def precision(self) -> float:
        return self.true_positives / self.flagged if self.flagged else 1.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.true_flaky if self.true_flaky else 1.0


def run_many(seeds: range, base: Scenario | None = None) -> Aggregate:
    """Pool results over many seeds; a single seed is too noisy to report."""
    base = base or Scenario()
    totals = Aggregate(len(seeds), 0, 0, 0, 0, 0, 0, 0, 0, 0.0, {})
    delays: list[float] = []
    for seed in seeds:
        scenario = Scenario(**{**base.__dict__, "seed": seed})
        m = run(scenario)
        totals.runs_ingested += m.runs_ingested
        totals.true_flaky += m.true_flaky
        totals.true_positives += m.true_positives
        totals.flagged += m.flagged
        totals.regressions += scenario.regressions
        totals.regressions_flagged += m.regressions_flagged
        totals.naive_flagged += m.naive_flagged
        totals.naive_false_positives += m.naive_false_positives
        if m.median_runs_to_detect is not None:
            delays.append(m.median_runs_to_detect)
        probs = dict(enumerate(scenario.flaky_probs))
        for name in m.missed:
            p = probs[int(name.removeprefix("flaky_"))]
            totals.missed_by_prob[p] = totals.missed_by_prob.get(p, 0) + 1
    totals.median_runs_to_detect = median(delays) if delays else 0.0
    return totals


def aggregate_markdown(base: Scenario, a: Aggregate) -> str:
    per_seed_tests = base.stable_tests + len(base.flaky_probs) + base.regressions
    missed = ", ".join(f"p={p}: {n}" for p, n in sorted(a.missed_by_prob.items())) or "none"
    naive_precision = (a.naive_flagged - a.naive_false_positives) / a.naive_flagged
    return "\n".join(
        [
            f"Pooled over {a.seeds} seeds; {a.runs_ingested} CI runs ingested in total "
            f"({per_seed_tests} tests per run).",
            "",
            "| Metric | TestLedger | Naive rule (failed >= 2 times) |",
            "|---|---|---|",
            f"| Tests flagged as flaky | {a.flagged} | {a.naive_flagged} |",
            f"| Precision | {a.precision:.3f} | {naive_precision:.3f} |",
            f"| Recall | {a.recall:.3f} | n/a |",
            f"| Real regressions flagged as flaky | {a.regressions_flagged} of {a.regressions} "
            f"| {a.naive_false_positives} |",
            f"| Median runs until quarantine (median of per-seed medians) "
            f"| {a.median_runs_to_detect} | n/a |",
            f"| Flaky tests missed, by failure probability | {missed} | |",
        ]
    )
