# 1-D Max Pooling Accelerator Results

## Interface

The only supported stream width is 32 bits. All words are produced/read through the Waveflow schemas or signed-int32 array utilities.

| Burst | Layout and framing |
| --- | --- |
| DATA command header | Four words: word 0 command code `DATA=0`, word 1 unsigned 32-bit `tx_id`, word 2 `N`, word 3 `W`; TLAST only on word 3. |
| END command header | Four words: `END=1`, then three zero words; TLAST only on word 3. |
| Input samples | One signed two's-complement int32 per word; exactly N words, TLAST on word N. |
| Response header | Two words: echoed `tx_id`, then `ceil(N/W)`; TLAST on word 2. |
| Results | One signed int32 maximum per word; TLAST on the final result. An absent result burst is used when an error occurs before any complete window can be emitted. |

Waveflow serialization evidence is in [layout.md](layout.md), [stage1_check.py](stage1_check.py), its fresh captured output [stage1_check_latest.log](impl/logs/stage1_check_latest.log), and the generated schemas under [impl/include](impl/include). For `tx_id=42, N=5, W=2`, the DATA header serializes to `00000000 0000002a 00000005 00000002`; the response header serializes to `0000002a 00000003`.

AXI-Lite carries start/done and read-only status: `halted` (1 bit), `error` (8-bit register carrying the enum), and `tx_id` (32 bits). Error codes are `0 NO_ERROR`, `1 EARLY_HEADER_TLAST`, `2 MISSING_HEADER_TLAST`, `3 INVALID_COMMAND`, `4 INVALID_PARAMETERS`, `5 EARLY_TLAST`, and `6 MISSING_TLAST`.

## Design

Each activation clears status, then reads four schema-serialized header words one at a time so header TLAST can be handled before any later beat is consumed. Header framing is checked before command decoding, and command decoding before parameter validation. Each DATA is validated before reading samples.

The sample loop keeps only the current window maximum, a sample count, and one pending completed result. A completed result is held until the next sample's TLAST status is known. On an input framing error, the sample carrying the error is not incorporated; the current window is discarded, while the preceding pending complete-window result is emitted with TLAST. This also handles an offending sample that would otherwise complete a window. No input is drained after an error.

The sample-processing loop is pipelined at II=1. The pending-result register allows output closure without attempting to change an already-transmitted word. No software timing model was added; latency/throughput measurements below come from Vitis RTL co-simulation.

## Targets

| Criterion | Required | Measured | Result | Evidence |
| --- | --- | --- | --- | --- |
| Input sample loop initiation interval | II=1 | II=1 | PASS | [maxpool_sample_loop_csynth_40.rpt](impl/logs/maxpool_sample_loop_csynth_40.rpt), [loop_df.csv](impl/results/loop_df.csv) |
| Input throughput under ready conditions | 1 sample/clock for the II=1 processing loop | RTL suite aggregate: 343 sample words / 3,307 total cycles = 0.103719 samples/clock; includes command/response and activation overhead, not a steady-state loop-only span | No separate aggregate target; loop II target PASS | [maxpool_cosim_40.rpt](impl/logs/maxpool_cosim_40.rpt), [cosim_measurements.json](impl/results/cosim_measurements.json) |
| RTL co-simulation activation latency | Measure; no numeric latency target | 10 cycles min, 69 average, 330 max | no target | [maxpool_cosim_40.rpt](impl/logs/maxpool_cosim_40.rpt), [cosim_measurements.json](impl/results/cosim_measurements.json) |
| RTL co-simulation transaction interval | Measure; no numeric interval target | 24 cycles min, 82 average, 344 max | no target | [maxpool_cosim_40.rpt](impl/logs/maxpool_cosim_40.rpt), [cosim_measurements.json](impl/results/cosim_measurements.json) |
| Estimated clock | Under 10 ns | 7.670 ns, 10.00 ns target, 2.70 ns uncertainty | PASS | [maxpool_csynth_40.rpt](impl/logs/maxpool_csynth_40.rpt), [vitis_synth_cosim_40.log](impl/logs/vitis_synth_cosim_40.log) |
| Clock uncertainty margin warning | Not a FAIL by prompt | Vitis warns effective delay budget is 7.300 ns while estimate is 7.670 ns | Not a failure under prompt rule | [vitis_synth_cosim_40.log](impl/logs/vitis_synth_cosim_40.log) |
| Resources | no target | 1,962 LUT, 1,656 FF, 0 BRAM, 0 DSP, 0 URAM | no target | [maxpool_csynth_40.rpt](impl/logs/maxpool_csynth_40.rpt), [resources.json](impl/results/resources.json) |
| Python model vs intent oracle | Exact, all scenarios | 40/40 pass | PASS | [check_model.json](impl/results/check_model.json) |
| C simulation vs intent oracle | Exact, all scenarios | 40/40 pass | PASS | [check_csim.json](impl/results/check_csim.json), [vitis_csim_40.log](impl/logs/vitis_csim_40.log) |
| RTL co-simulation vs intent oracle | Exact, all scenarios in one testbench run | 40/40 pass; Vitis reports C/RTL co-simulation PASS | PASS | [check_cosim.json](impl/results/check_cosim.json), [maxpool_cosim_40.rpt](impl/logs/maxpool_cosim_40.rpt), [xsim_cosim_40.log](impl/logs/xsim_cosim_40.log) |

## Verification

The model's hand-computed cases produced `[7, 9, 4]`, `[-3, -1]`, `[7]`, and `[2, -1, 0, 6]`. The independent Stage 1 NumPy slice oracle checks against `maxpool_eval`; the reproducible script and fresh captured output with two rejected wrong-output probes are in [stage1_check.py](stage1_check.py) and [stage1_check_latest.log](impl/logs/stage1_check_latest.log). All 40 model, C-simulation, and co-simulation comparisons have empty problem lists in [check_model.json](impl/results/check_model.json), [check_csim.json](impl/results/check_csim.json), and [check_cosim.json](impl/results/check_cosim.json). Scenario-level status and outputs are retained under [impl/data/w32](impl/data/w32).

| Scenario | Model / C sim / RTL cosim | Error status | Fresh activation after error |
| --- | --- | --- | --- |
| `worked_n5_w2` | PASS / PASS / PASS | none | n/a |
| `worked_negative` | PASS / PASS / PASS | none | n/a |
| `worked_w_gt_n` | PASS / PASS / PASS | none | n/a |
| `worked_w1` | PASS / PASS / PASS | none | n/a |
| `minimum_n_w` | PASS / PASS / PASS | none | n/a |
| `maximum_n_w` | PASS / PASS / PASS | none | n/a |
| `non_divisible` | PASS / PASS / PASS | none | n/a |
| `window_exceeds_n` | PASS / PASS / PASS | none | n/a |
| `all_negative` | PASS / PASS / PASS | none | n/a |
| `repeated_values` | PASS / PASS / PASS | none | n/a |
| `signed_extremes` | PASS / PASS / PASS | none | n/a |
| `multiple_data_then_end` | PASS / PASS / PASS | none | n/a |
| `unknown_command` | PASS / PASS / PASS | halted=1, error=3, tx_id=21; queued data unread | `fresh_after_unknown_command`: PASS |
| `fresh_after_unknown_command` | PASS / PASS / PASS | cleared status | verified |
| `invalid_n_zero` | PASS / PASS / PASS | halted=1, error=4, tx_id=23; sample data unread | `fresh_after_invalid_n_zero`: PASS |
| `fresh_after_invalid_n_zero` | PASS / PASS / PASS | cleared status | verified |
| `invalid_n_257` | PASS / PASS / PASS | halted=1, error=4, tx_id=25; full sample burst unread | `fresh_after_invalid_n_257`: PASS |
| `fresh_after_invalid_n_257` | PASS / PASS / PASS | cleared status | verified |
| `invalid_w_zero` | PASS / PASS / PASS | halted=1, error=4, tx_id=27; sample data unread | `fresh_after_invalid_w_zero`: PASS |
| `fresh_after_invalid_w_zero` | PASS / PASS / PASS | cleared status | verified |
| `invalid_w_nine` | PASS / PASS / PASS | halted=1, error=4, tx_id=29; sample data unread | `fresh_after_invalid_w_nine`: PASS |
| `fresh_after_invalid_w_nine` | PASS / PASS / PASS | cleared status | verified |
| `early_tlast_inside_window` | PASS / PASS / PASS | halted=1, error=5, tx_id=31; incomplete window discarded | `fresh_after_early_inside`: PASS |
| `fresh_after_early_inside` | PASS / PASS / PASS | cleared status | verified |
| `early_tlast_completes_window` | PASS / PASS / PASS | halted=1, error=5, tx_id=33; offending sample discarded and its window not emitted | `fresh_after_early_complete`: PASS |
| `fresh_after_early_complete` | PASS / PASS / PASS | cleared status | verified |
| `early_tlast_no_result` | PASS / PASS / PASS | halted=1, error=5, tx_id=35; no empty result burst | `fresh_after_early_no_result`: PASS |
| `fresh_after_early_no_result` | PASS / PASS / PASS | cleared status | verified |
| `missing_data_tlast` | PASS / PASS / PASS | halted=1, error=6, tx_id=37; final incomplete window discarded | `fresh_after_missing_data_tlast`: PASS |
| `fresh_after_missing_data_tlast` | PASS / PASS / PASS | cleared status | verified |
| `early_header_before_tx_id` | PASS / PASS / PASS | halted=1, error=1, tx_id=0; later header words unread | `fresh_after_early_header_before_tx_id`: PASS |
| `fresh_after_early_header_before_tx_id` | PASS / PASS / PASS | cleared status | verified |
| `early_header_after_tx_id` | PASS / PASS / PASS | halted=1, error=1, tx_id=40; later header words unread | `fresh_after_early_header_after_tx_id`: PASS |
| `fresh_after_early_header_after_tx_id` | PASS / PASS / PASS | cleared status | verified |
| `missing_header_tlast` | PASS / PASS / PASS | halted=1, error=2, tx_id=42; no later input consumed | `fresh_after_missing_header_tlast`: PASS |
| `fresh_after_missing_header_tlast` | PASS / PASS / PASS | cleared status | verified |
| `early_header_beats_unknown_code` | PASS / PASS / PASS | halted=1, error=1, tx_id=0; framing error wins | see Open Items |
| `fresh_after_early_header_beats_unknown_code` | PASS / PASS / PASS | cleared status | verified immediately after preceding error |
| `missing_header_beats_unknown_code` | PASS / PASS / PASS | halted=1, error=2, tx_id=45; framing error wins | see Open Items |
| `fresh_after_missing_header_beats_unknown_code` | PASS / PASS / PASS | cleared status | verified immediately after preceding error |

Evidence for every row is the corresponding key in the three check JSON files above; status, response words/TLAST, and unread-word evidence for a specific activation are under `impl/data/w32/<scenario>/{model,csim,cosim}/`. [csim_scenarios_summary.log](impl/logs/csim_scenarios_summary.log) records all 40 C-simulation statuses and unread counts.

## Assumptions

- `tx_id` is unsigned 32-bit, as documented and serialized in the approved `layout.md`; the prompt did not specify its width.
- END uses code 1 and zeroes its unused fields. Any nonzero code other than DATA=0 or END=1 is invalid.
- The header is four separate schema words in a fixed order; header framing is checked before command and parameter validation.
- For an early or missing sample TLAST, the sample carrying the framing error is read to detect the error but is not included in any window. Only maxima from strictly preceding complete windows are emitted.
- A response header is emitted after a valid DATA header and before sample processing. If the error occurs before any result is emitted, no empty result burst is created.
- The ECS module provides Vitis/Vivado 2026.1; that installed version was used even though the generic frame reference names Vitis HLS 2025.1.
- The full-suite aggregate throughput divides all valid-DATA sample words consumed by the co-sim report's total execution cycles. It includes activation/control and command/response overhead; it is not presented as the steady-state loop rate.

## Open Items

- The original unedited-scaffold `check_pysim` output was overwritten by later Stage 2 output, so its historical result cannot be independently checked from a retained artifact. The currently retained [stage1_check_latest.log](impl/logs/stage1_check_latest.log) is a fresh, reproducible Stage 1 check run after implementation, not evidence of the original scaffold run.
- Vitis HLS 2026.1 was supplied by the specified ECS module; the frame's generic Vitis 2025.1 version was unavailable through that module.
- The single co-simulation report provides aggregate latency/interval statistics, not an isolated per-sample steady-state throughput trace. Aggregate throughput is reported explicitly; the II=1 claim is measured in synthesis.

## Run History

| Prompt/version | What was run or changed | Outcome and evidence |
| --- | --- | --- |
| Take-home prompt v2; Stage 1 approved | Original scaffold `py_sim` checkpoint passed in terminal output, but its check artifact was later overwritten; reran the approved Stage 1 check after the rubric review | Current reproducible rerun passed, [stage1_check_latest.log](impl/logs/stage1_check_latest.log); this is not represented as the historical scaffold log |
| Take-home prompt v2; Stage 2 revised suite | Added two fresh-success scenarios immediately after the priority-error cases; generated and checked the 40-entry model suite | 40/40 PASS, [check_model.json](impl/results/check_model.json) |
| Take-home prompt v2; Stage 2 revised suite | Python functional body on all well-formed scenarios | 26/26 PASS, [check_pysim.json](impl/results/check_pysim.json) |
| Take-home prompt v2; Stage 2 revised suite | Vitis C simulation over 40 ordered fresh-stream activations | 40/40 PASS, [check_csim.json](impl/results/check_csim.json), raw [vitis_csim_40.log](impl/logs/vitis_csim_40.log), summary [csim_scenarios_summary.log](impl/logs/csim_scenarios_summary.log) |
| Take-home prompt v2; Stage 2 revised suite | Vitis synthesis and one comprehensive RTL co-simulation run over 40 activations | 40/40 PASS, [check_cosim.json](impl/results/check_cosim.json), [maxpool_csynth_40.rpt](impl/logs/maxpool_csynth_40.rpt), [maxpool_cosim_40.rpt](impl/logs/maxpool_cosim_40.rpt), [xsim_cosim_40.log](impl/logs/xsim_cosim_40.log) |
| Take-home prompt v2; Stage 2 revised suite | Parsed final synthesis resources and 40-case co-sim latency/throughput | II=1 and estimated clock 7.670 ns; [resources.json](impl/results/resources.json), [loop_df.csv](impl/results/loop_df.csv), [cosim_measurements.json](impl/results/cosim_measurements.json) |