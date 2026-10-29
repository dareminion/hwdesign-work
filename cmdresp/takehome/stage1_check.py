"""Run the frozen Stage 1 examples, independent oracle and schema layout checks."""
from __future__ import annotations

from maxpool import Command, Int32, PoolCmdHdr, PoolRespHdr, maxpool_eval
from scenarios import SCENARIOS, check_values, expected_activation, expected_values, validate_stage1
from waveflow.hw.arrayutils import write_array


def main() -> None:
    validate_stage1()
    examples = [
        ((3, 7, -2, 9, 4), 2),
        ((-8, -3, -10, -1), 2),
        ((5, 1, 7), 8),
        ((2, -1, 0, 6), 1),
    ]
    for samples, window in examples:
        print(f"worked example N={len(samples)} W={window}: "
              f"{list(map(int, maxpool_eval(samples, window)))}")
    early = next(s for s in SCENARIOS if s.name == "early_data_tlast")
    missing = next(s for s in SCENARIOS if s.name == "missing_data_tlast")
    print("early data TLAST expected completed-window results:",
          expected_values(early.commands[0]))
    print("missing data TLAST expected completed-window results:",
          expected_values(missing.commands[0]))
    print("early header expected activation:", expected_activation(
        next(s for s in SCENARIOS if s.name == "early_header_before_tx_id")))

    for name, expected, actual in [
        ("wrong final value", [7, 9, 4], [7, 9, 3]),
        ("wrong output length", [7, 9, 4], [7, 9]),
    ]:
        try:
            check_values(name, expected, actual)
        except AssertionError as error:
            print(f"checker rejected {name}: {error}")
        else:
            raise AssertionError(f"checker accepted {name}")

    command = PoolCmdHdr()
    command.cmd_type = Command.DATA
    command.tx_id = 42
    command.n = 5
    command.window = 2
    end = PoolCmdHdr()
    end.cmd_type = Command.END
    end.tx_id = 0
    end.n = 0
    end.window = 0
    response = PoolRespHdr()
    response.tx_id = 42
    response.output_count = 3
    print("DATA header:", [f"0x{int(word):08x}" for word in command.serialize(32)])
    print("END header:", [f"0x{int(word):08x}" for word in end.serialize(32)])
    print("response header:", [f"0x{int(word):08x}" for word in response.serialize(32)])
    print("signed sample words:", [
        f"0x{int(word):08x}"
        for word in write_array([-2147483648, 2147483647], elem_type=Int32, word_bw=32)
    ])
    print(f"valid scenario activations: {len(SCENARIOS)}")
    print("Stage 1 checks passed")


if __name__ == "__main__":
    main()