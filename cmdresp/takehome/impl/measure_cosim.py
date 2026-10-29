"""Derive aggregate RTL throughput from retained cosim evidence and scenario intent."""
from __future__ import annotations

import re
import json
from pathlib import Path

import scenarios
from maxpool import MaxpoolCmdType


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "w32_proj" / "solution1" / "sim" / "report" / "maxpool_cosim.rpt"


def main() -> None:
    report_text = REPORT.read_text(encoding="utf-8")
    match = re.search(r"\|\s*Verilog\|\s*Pass\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
                      r"\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|",
                      report_text)
    if not match:
        raise RuntimeError(f"could not parse Verilog timing row from {REPORT}")
    latency_min, latency_avg, latency_max, interval_min, interval_avg, interval_max, total_cycles = (
        int(value) for value in match.groups())
    samples = outputs = 0
    for scenario in scenarios.scenario_map().values():
        for item in scenario.commands:
            if item.code != int(MaxpoolCmdType.DATA):
                continue
            if not (1 <= item.n <= 256 and 1 <= item.window <= 8):
                continue
            sent = item.sample_tlast_after if item.sample_tlast_after is not None else item.n
            samples += sent
            if item.sample_tlast_after is not None:
                outputs += (sent - 1) // item.window
            elif item.sample_final_tlast:
                outputs += (sent + item.window - 1) // item.window
            else:
                outputs += (sent - 1) // item.window
    measurements = {
        "source_report": str(REPORT.relative_to(ROOT)),
        "latency_cycles": {"min": latency_min, "average": latency_avg, "max": latency_max},
        "interval_cycles": {"min": interval_min, "average": interval_avg, "max": interval_max},
        "total_execution_cycles": total_cycles,
        "scenario_count": len(scenarios.scenario_map()),
        "sample_words_consumed_from_intents": samples,
        "result_words_emitted_from_intents": outputs,
        "aggregate_sample_words_per_cycle": samples / total_cycles,
        "aggregate_result_words_per_cycle": outputs / total_cycles,
    }
    path = ROOT / "results" / "cosim_measurements.json"
    path.write_text(json.dumps(measurements, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(measurements, indent=2))


if __name__ == "__main__":
    main()