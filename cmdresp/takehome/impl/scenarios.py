"""Max-pooling scenario intents, independent expectations, and exact artifact checks."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from maxpool import (
    Int32,
    MaxpoolCmdHdr,
    MaxpoolCmdType,
    MaxpoolError,
    MaxpoolRespHdr,
    UInt32,
    maxpool_eval,
)
from waveflow.hw.arrayutils import write_array
from waveflow.utils.burst_io import StreamBurst, read_bursts, write_bursts


@dataclass(frozen=True)
class CommandIntent:
    code: int
    tx_id: int
    n: int
    window: int
    samples: tuple[int, ...] = ()
    header_tlast_after: int | None = 4
    sample_tlast_after: int | None = None
    sample_final_tlast: bool = True
    trailing_samples: tuple[int, ...] = ()


@dataclass(frozen=True)
class Scenario:
    commands: tuple[CommandIntent, ...]
    end: bool = True


def command(tx_id: int, samples: tuple[int, ...], window: int) -> CommandIntent:
    return CommandIntent(int(MaxpoolCmdType.DATA), tx_id, len(samples), window, samples)


def scenario_map() -> dict[str, Scenario]:
    max_data = tuple(range(-128, 128))
    cases = {
        "worked_n5_w2": Scenario((command(1, (3, 7, -2, 9, 4), 2),)),
        "worked_negative": Scenario((command(2, (-8, -3, -10, -1), 2),)),
        "worked_w_gt_n": Scenario((command(3, (5, 1, 7), 8),)),
        "worked_w1": Scenario((command(4, (2, -1, 0, 6), 1),)),
        "minimum_n_w": Scenario((command(5, (-(1 << 31),), 1),)),
        "maximum_n_w": Scenario((command(6, max_data, 8),)),
        "non_divisible": Scenario((command(7, (8, -4, 12, 3, 12), 2),)),
        "window_exceeds_n": Scenario((command(8, (-4, -11, -2), 8),)),
        "all_negative": Scenario((command(9, (-9, -2, -11, -4, -7), 2),)),
        "repeated_values": Scenario((command(10, (4, 4, 4, -3, -3, -3), 3),)),
        "signed_extremes": Scenario((command(11, (-(1 << 31), (1 << 31) - 1, 0, -1), 2),)),
        "multiple_data_then_end": Scenario((
            command(12, (3, 7, -2, 9, 4), 2),
            command(13, (5, 1, 7), 8),
            command(14, (-(1 << 31), (1 << 31) - 1), 1),
        )),
        "unknown_command": Scenario((CommandIntent(
            7, 21, 2, 1, (), trailing_samples=(101, 202),
        ),), end=False),
        "fresh_after_unknown_command": Scenario((command(22, (1, -2), 1),)),
        "invalid_n_zero": Scenario((CommandIntent(
            0, 23, 0, 1, (), trailing_samples=(31, 32),
        ),), end=False),
        "fresh_after_invalid_n_zero": Scenario((command(24, (9,), 1),)),
        "invalid_n_257": Scenario((CommandIntent(
            0, 25, 257, 1, tuple(range(257)),
        ),), end=False),
        "fresh_after_invalid_n_257": Scenario((command(26, (9,), 1),)),
        "invalid_w_zero": Scenario((CommandIntent(
            0, 27, 2, 0, (1, 2),
        ),), end=False),
        "fresh_after_invalid_w_zero": Scenario((command(28, (9,), 1),)),
        "invalid_w_nine": Scenario((CommandIntent(
            0, 29, 2, 9, (1, 2),
        ),), end=False),
        "fresh_after_invalid_w_nine": Scenario((command(30, (9,), 1),)),
        "early_tlast_inside_window": Scenario((CommandIntent(
            0, 31, 5, 2, (3, 7, -2, 9, 4), sample_tlast_after=3,
        ),), end=False),
        "fresh_after_early_inside": Scenario((command(32, (9, 8), 2),)),
        "early_tlast_completes_window": Scenario((CommandIntent(
            0, 33, 5, 2, (3, 7, -2, 9, 4), sample_tlast_after=4,
        ),), end=False),
        "fresh_after_early_complete": Scenario((command(34, (9, 8), 2),)),
        "early_tlast_no_result": Scenario((CommandIntent(
            0, 35, 5, 2, (3, 7, -2, 9, 4), sample_tlast_after=1,
        ),), end=False),
        "fresh_after_early_no_result": Scenario((command(36, (9,), 1),)),
        "missing_data_tlast": Scenario((CommandIntent(
            0, 37, 5, 2, (3, 7, -2, 9, 4), sample_final_tlast=False,
        ),), end=False),
        "fresh_after_missing_data_tlast": Scenario((command(38, (9, 8), 2),)),
        "early_header_before_tx_id": Scenario((CommandIntent(
            7, 0xA1, 2, 1, (), header_tlast_after=1,
            trailing_samples=(101, 202),
        ),), end=False),
        "fresh_after_early_header_before_tx_id": Scenario((command(39, (6,), 1),)),
        "early_header_after_tx_id": Scenario((CommandIntent(
            0, 40, 2, 1, (), header_tlast_after=2,
            trailing_samples=(101, 202),
        ),), end=False),
        "fresh_after_early_header_after_tx_id": Scenario((command(41, (6,), 1),)),
        "missing_header_tlast": Scenario((CommandIntent(
            0, 42, 2, 1, (), header_tlast_after=None,
            trailing_samples=(101, 202),
        ),), end=False),
        "fresh_after_missing_header_tlast": Scenario((command(43, (6,), 1),)),
        "early_header_beats_unknown_code": Scenario((CommandIntent(
            9, 44, 1, 1, (), header_tlast_after=1,
        ),), end=False),
        "fresh_after_early_header_beats_unknown_code": Scenario((command(46, (7, -3), 1),)),
        "missing_header_beats_unknown_code": Scenario((CommandIntent(
            9, 45, 1, 1, (), header_tlast_after=None,
        ),), end=False),
        "fresh_after_missing_header_beats_unknown_code": Scenario((command(47, (-5, 2), 1),)),
    }
    return cases


def _header(intent: CommandIntent) -> np.ndarray:
    header = MaxpoolCmdHdr()
    header.cmd_type = intent.code
    header.tx_id = intent.tx_id
    header.n = intent.n
    header.window = intent.window
    return header.serialize(word_bw=32)


def _sample_words(samples: tuple[int, ...]) -> np.ndarray:
    return write_array(samples, elem_type=Int32, word_bw=32)


def stimulus(sc: Scenario) -> list[StreamBurst]:
    bursts: list[StreamBurst] = []
    for intent in sc.commands:
        header_words = _header(intent)
        if intent.header_tlast_after is not None and intent.header_tlast_after < 4:
            split = intent.header_tlast_after
            bursts.append(StreamBurst(header_words[:split], True))
            if split < 4:
                bursts.append(StreamBurst(header_words[split:], False))
        else:
            bursts.append(StreamBurst(header_words, intent.header_tlast_after == 4))
        sent = intent.n
        sample_tlast = intent.sample_final_tlast
        if intent.sample_tlast_after is not None:
            sent = intent.sample_tlast_after
            sample_tlast = True
        if sent:
            bursts.append(StreamBurst(_sample_words(intent.samples[:sent]), sample_tlast))
        if intent.trailing_samples:
            bursts.append(StreamBurst(_sample_words(intent.trailing_samples)))
    if sc.end:
        end = CommandIntent(int(MaxpoolCmdType.END), 0, 0, 0)
        bursts.append(StreamBurst(_header(end)))
    return bursts


def _valid_command(intent: CommandIntent) -> bool:
    return intent.code == int(MaxpoolCmdType.DATA) and 1 <= intent.n <= 256 and 1 <= intent.window <= 8


def expected(sc: Scenario) -> tuple[list[StreamBurst], dict, int, int]:
    """Independent expected output/status/consumption computed from scenario intent."""
    output: list[StreamBurst] = []
    status = {"halted": 0, "error": int(MaxpoolError.NO_ERROR), "tx_id": 0}
    total_words = sum(len(b.words) for b in stimulus(sc))
    consumed = 0

    for intent in sc.commands:
        header_len = len(_header(intent))
        if intent.header_tlast_after is not None and intent.header_tlast_after < 4:
            consumed += intent.header_tlast_after
            status = {"halted": 1, "error": int(MaxpoolError.EARLY_HEADER_TLAST), "tx_id":
                      intent.tx_id if intent.header_tlast_after >= 2 else 0}
            break
        if intent.header_tlast_after is None:
            consumed += header_len
            status = {"halted": 1, "error": int(MaxpoolError.MISSING_HEADER_TLAST), "tx_id": intent.tx_id}
            break
        consumed += header_len
        if intent.code == int(MaxpoolCmdType.END):
            break
        if intent.code != int(MaxpoolCmdType.DATA):
            status = {"halted": 1, "error": int(MaxpoolError.INVALID_COMMAND), "tx_id": intent.tx_id}
            break
        if not 1 <= intent.n <= 256 or not 1 <= intent.window <= 8:
            status = {"halted": 1, "error": int(MaxpoolError.INVALID_PARAMETERS), "tx_id": intent.tx_id}
            break

        resp = MaxpoolRespHdr()
        resp.tx_id = intent.tx_id
        resp.output_count = (intent.n + intent.window - 1) // intent.window
        output.append(StreamBurst(resp.serialize(word_bw=32)))
        sent = intent.sample_tlast_after if intent.sample_tlast_after is not None else intent.n
        consumed += sent
        early = sent < intent.n and intent.sample_tlast_after is not None
        missing = sent == intent.n and not intent.sample_final_tlast
        if early:
            usable = ((sent - 1) // intent.window) * intent.window
            status = {"halted": 1, "error": int(MaxpoolError.EARLY_TLAST), "tx_id": intent.tx_id}
        elif missing:
            usable = ((intent.n - 1) // intent.window) * intent.window
            status = {"halted": 1, "error": int(MaxpoolError.MISSING_TLAST), "tx_id": intent.tx_id}
        else:
            usable = intent.n
        values = np.asarray(intent.samples[:usable], dtype=np.int64)
        pooled = [
            int(np.max(values[start:min(start + intent.window, usable)]))
            for start in range(0, usable, intent.window)
        ]
        if pooled:
            output.append(StreamBurst(_sample_words(tuple(pooled))))
        if early or missing:
            break

    else:
        if sc.end:
            consumed += len(_header(CommandIntent(int(MaxpoolCmdType.END), 0, 0, 0)))

    expected_unread = total_words - consumed
    return output, status, expected_unread, consumed


def write_scenarios(data_dir: Path) -> list[str]:
    data_dir.mkdir(parents=True, exist_ok=True)
    names = list(scenario_map())
    for name, sc in scenario_map().items():
        directory = data_dir / name
        write_bursts(stimulus(sc), directory / "in")
        exp, status, unread, consumed = expected(sc)
        write_bursts(exp, directory / "expected")
        (directory / "expected_status.json").write_text(json.dumps(status) + "\n", encoding="utf-8")
        (directory / "expected_consumption.json").write_text(
            json.dumps({"unread_words": unread, "consumed_words": consumed}) + "\n", encoding="utf-8")
    (data_dir / "scenarios.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    return names


def check(data_dir: Path, stage: str, names: list[str] | None = None) -> dict[str, list[str]]:
    names = names or (data_dir / "scenarios.txt").read_text(encoding="utf-8").split()
    report: dict[str, list[str]] = {}
    for name in names:
        directory = data_dir / name
        out_dir = directory / stage
        problems: list[str] = []
        if not (out_dir / "words.bin").exists():
            report[name] = [f"no {stage} output in {out_dir}"]
            continue
        wanted = read_bursts(directory / "expected")
        actual = read_bursts(out_dir)
        wanted_status = json.loads((directory / "expected_status.json").read_text(encoding="utf-8"))
        if len(actual) != len(wanted):
            problems.append(f"{len(actual)} bursts, expected {len(wanted)}")
        for index, (want, got) in enumerate(zip(wanted, actual)):
            if want.tlast != got.tlast:
                problems.append(f"burst {index}: TLAST {got.tlast}, expected {want.tlast}")
            want_words = np.asarray(want.words, dtype=np.uint64)
            got_words = np.asarray(got.words, dtype=np.uint64)
            if not np.array_equal(want_words, got_words):
                problems.append(f"burst {index}: words {got_words.tolist()}, expected {want_words.tolist()}")
        status_path = out_dir / "status.json"
        if not status_path.exists():
            problems.append("missing status.json")
        elif json.loads(status_path.read_text(encoding="utf-8")) != wanted_status:
            problems.append(f"status differs: got {status_path.read_text().strip()}, expected {wanted_status}")
        unread_path = out_dir / "unread_words.txt"
        expected_unread = json.loads((directory / "expected_consumption.json").read_text())[
            "unread_words"]
        if not unread_path.exists():
            problems.append("missing unread_words.txt")
        elif int(unread_path.read_text().strip()) != expected_unread:
            problems.append(f"unread words {unread_path.read_text().strip()}, expected {expected_unread}")
        if wanted_status["halted"] and actual and not actual[-1].tlast:
            problems.append("error output burst was not closed with TLAST")
        report[name] = problems
    return report