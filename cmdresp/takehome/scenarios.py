"""Stage 1 scenario intents and independent expected max-pooling results."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from maxpool import maxpool_eval


@dataclass(frozen=True)
class DataIntent:
    tx_id: int
    samples: tuple[int, ...]
    window: int
    tlast_after: int | None = None
    final_tlast: bool = True


@dataclass(frozen=True)
class Scenario:
    name: str
    commands: tuple[DataIntent, ...] = ()
    command_code: int = 0
    header_tx_id: int = 0
    header_tlast_after: int | None = 4
    expected_error: str = "NO_ERROR"
    expected_tx_id: int = 0
    note: str = ""
    queued_data: tuple[DataIntent, ...] = ()


@dataclass(frozen=True)
class ExpectedActivation:
    response_headers: tuple[tuple[int, int], ...]
    result_bursts: tuple[tuple[int, ...], ...]
    halted: int
    error: str
    tx_id: int
    unread_commands: tuple[int, ...]


def data(tx_id: int, samples: tuple[int, ...], window: int) -> DataIntent:
    return DataIntent(tx_id, samples, window)


SCENARIOS = [
    Scenario("worked_example_5_2", (data(1, (3, 7, -2, 9, 4), 2),)),
    Scenario("worked_example_negative", (data(2, (-8, -3, -10, -1), 2),)),
    Scenario("worked_example_window_gt_n", (data(3, (5, 1, 7), 8),)),
    Scenario("worked_example_window_1", (data(4, (2, -1, 0, 6), 1),)),
    Scenario("minimum_n_w", (data(5, (-(1 << 31),), 1),)),
    Scenario("maximum_n_w", (data(6, tuple(range(-128, 128)), 8),)),
    Scenario("non_divisible", (data(7, (8, -4, 12, 3, 12, -9, 1), 3),)),
    Scenario("all_negative", (data(8, (-9, -2, -11, -4, -7), 2),)),
    Scenario("repeated_values", (data(9, (4, 4, 4, -3, -3, -3), 3),)),
    Scenario("signed_extremes", (data(10, (-(1 << 31), (1 << 31) - 1, 0, -1), 2),)),
    Scenario("multiple_data_then_end", (
        data(11, (3, 7, -2, 9, 4), 2),
        data(12, (5, 1, 7), 8),
        data(13, (-(1 << 31), (1 << 31) - 1), 1),
    )),
    Scenario("unknown_command", command_code=2, header_tx_id=21, expected_error="INVALID_COMMAND",
             expected_tx_id=21, note="Must leave appended DATA command unread.",
             queued_data=(data(40, (1, 2), 1),)),
    Scenario("fresh_after_unknown_command", (data(22, (1, -2), 1),)),
    Scenario("invalid_n_zero", (data(23, (), 1),), expected_error="INVALID_PARAMETERS",
             expected_tx_id=23, note="No sample burst may be consumed.",
             queued_data=(data(41, (1, 2), 1),)),
    Scenario("fresh_after_invalid_n_zero", (data(24, (9,), 1),)),
    Scenario("invalid_n_257", (data(25, tuple(range(257)), 1),), expected_error="INVALID_PARAMETERS",
             expected_tx_id=25, note="No sample burst may be consumed.",
             queued_data=(data(42, (1, 2), 1),)),
    Scenario("fresh_after_invalid_n_257", (data(26, (9,), 1),)),
    Scenario("invalid_w_zero", (data(27, (1, 2), 0),), expected_error="INVALID_PARAMETERS",
             expected_tx_id=27, note="No sample burst may be consumed.",
             queued_data=(data(43, (1, 2), 1),)),
    Scenario("fresh_after_invalid_w_zero", (data(28, (9,), 1),)),
    Scenario("invalid_w_nine", (data(29, (1, 2), 9),), expected_error="INVALID_PARAMETERS",
             expected_tx_id=29, note="No sample burst may be consumed.",
             queued_data=(data(44, (1, 2), 1),)),
    Scenario("fresh_after_invalid_w_nine", (data(30, (9,), 1),)),
    Scenario("early_data_tlast", (DataIntent(31, (3, 7, -2, 9, 4), 2, tlast_after=3),),
             expected_error="EARLY_TLAST", expected_tx_id=31),
    Scenario("fresh_after_early_data_tlast", (data(32, (9, 8), 2),)),
    Scenario("missing_data_tlast", (DataIntent(33, (3, 7, -2, 9, 4), 2, final_tlast=False),),
             expected_error="MISSING_TLAST", expected_tx_id=33),
    Scenario("fresh_after_missing_data_tlast", (data(34, (9, 8), 2),)),
    Scenario("early_header_before_tx_id", header_tlast_after=1,
             expected_error="EARLY_HEADER_TLAST", expected_tx_id=0,
             queued_data=(data(45, (1, 2), 1),)),
    Scenario("fresh_after_early_header_before_tx_id", (data(35, (6,), 1),)),
    Scenario("early_header_after_tx_id", header_tx_id=36, header_tlast_after=2,
             expected_error="EARLY_HEADER_TLAST", expected_tx_id=36,
             queued_data=(data(46, (1, 2), 1),)),
    Scenario("fresh_after_early_header_after_tx_id", (data(37, (6,), 1),)),
    Scenario("missing_header_tlast", header_tx_id=38, header_tlast_after=None,
             expected_error="MISSING_HEADER_TLAST", expected_tx_id=38,
             queued_data=(data(47, (1, 2), 1),)),
    Scenario("fresh_after_missing_header_tlast", (data(39, (6,), 1),)),
]


def expected_values(intent: DataIntent) -> list[int]:
    """Independent oracle: NumPy max over explicit slices from scenario intent."""
    samples = np.asarray(intent.samples, dtype=np.int64)
    usable_count = samples.size
    if intent.tlast_after is not None and intent.tlast_after < samples.size:
        usable_count = (intent.tlast_after - 1) // intent.window * intent.window
    elif not intent.final_tlast:
        usable_count = (samples.size - 1) // intent.window * intent.window
    return [
        int(np.max(samples[start:min(start + intent.window, usable_count)]))
        for start in range(0, usable_count, intent.window)
    ] if usable_count else []


def expected_activation(scenario: Scenario) -> ExpectedActivation:
    """Compute expected protocol outcomes from intent, never from encoded stimulus."""
    headers: list[tuple[int, int]] = []
    results: list[tuple[int, ...]] = []
    if scenario.expected_error in {
        "EARLY_HEADER_TLAST", "MISSING_HEADER_TLAST", "INVALID_COMMAND", "INVALID_PARAMETERS"
    }:
        return ExpectedActivation(
            (), (), 1, scenario.expected_error, scenario.expected_tx_id,
            tuple(intent.tx_id for intent in scenario.queued_data),
        )

    for index, intent in enumerate(scenario.commands):
        if not (1 <= len(intent.samples) <= 256 and 1 <= intent.window <= 8):
            return ExpectedActivation(
                tuple(headers), tuple(results), 1, "INVALID_PARAMETERS", intent.tx_id,
                tuple(item.tx_id for item in scenario.queued_data),
            )
        values = expected_values(intent)
        headers.append((intent.tx_id, (len(intent.samples) + intent.window - 1) // intent.window))
        if values:
            results.append(tuple(values))
        if intent.tlast_after is not None and intent.tlast_after < len(intent.samples):
            return ExpectedActivation(
                tuple(headers), tuple(results), 1, "EARLY_TLAST", intent.tx_id,
                tuple(item.tx_id for item in scenario.queued_data),
            )
        if not intent.final_tlast:
            return ExpectedActivation(
                tuple(headers), tuple(results), 1, "MISSING_TLAST", intent.tx_id,
                tuple(item.tx_id for item in scenario.queued_data),
            )
        if index == len(scenario.commands) - 1:
            return ExpectedActivation(tuple(headers), tuple(results), 0, "NO_ERROR", 0, ())
    return ExpectedActivation(tuple(headers), tuple(results), 0, "NO_ERROR", 0, ())


def check_values(name: str, expected: list[int], actual: list[int]) -> None:
    if expected != actual:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")


def validate_stage1() -> None:
    for scenario in SCENARIOS:
        expected = expected_activation(scenario)
        assert expected.halted == int(scenario.expected_error != "NO_ERROR")
        assert expected.error == scenario.expected_error
        assert expected.tx_id == scenario.expected_tx_id
        for intent in scenario.commands:
            if (scenario.expected_error == "NO_ERROR"
                    and 1 <= len(intent.samples) <= 256
                    and 1 <= intent.window <= 8):
                check_values(
                    scenario.name,
                    expected_values(intent),
                    [int(value) for value in maxpool_eval(intent.samples, intent.window)],
                )