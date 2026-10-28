# 1-D Max Pooling Accelerator

Build a Vitis HLS kernel in this folder following the Waveflow stream_inband example (https://sdrangan.github.io/waveflow/docs/examples/stream_inband/) and the command-response pattern.

## Operation

Implement a streaming 1-D max pooling accelerator. For each DATA command, receive N signed integer samples and a window size W. Divide the input into consecutive, non-overlapping windows of W samples and output the maximum of each window.

For output index j:

y[j] = max(x[jW], ..., x[min((j+1)W, N)-1])

The final incomplete window produces the maximum of its available samples. The output length is ceil(N/W). No padding is used.

Example: N=5, W=2, input [3, 7, -2, 9, 4] produces [7, 9, 4].

Each DATA command is independent. Window contents, parameters, and intermediate state do not carry over between commands or activations.

## Requirements

Input and output samples are signed 32-bit two's-complement integers. Comparisons are exact; no rounding, scaling, or saturation is required.

Supported input length: 1 <= N <= 256.
Supported window size: 1 <= W <= 8. W may exceed N; in that case the output is the maximum of all N samples.

Use streaming processing with no full-input buffer. Only the current window maximum and necessary control state need to be stored.

Only a 32-bit AXI-Stream data width is required for this project. No wider-stream build variants are required.

Target an initiation interval of 1 for the input-sample processing loop, accepting one 32-bit sample per clock when input and output are ready. Throughput is measured in samples per clock, equivalent to words per clock for this 32-bit interface. Measure actual latency and throughput in RTL co-simulation. Use the reference example's FPGA part and clock configuration; the estimated clock target is under 10 ns.

## Commands and parameters

Support DATA and END commands.

DATA carries tx_id, N, and W in its command header, followed by a burst containing exactly N samples. Validate N and W before reading any sample data. Each successful DATA command produces a response header echoing tx_id and the output count ceil(N/W), followed by that many results.

END carries no sample data and terminates the activation successfully. It produces no response data.

Every command header is a separate TLAST-terminated burst, followed by a separate TLAST-terminated sample burst for DATA. The same framing applies to response headers and result bursts.

Invalid parameters are N=0, N>256, W=0, or W>8. N and W are checked before any sample is read. Unknown command codes are invalid.

Choose consistent command codes, header layouts, and word packing following Waveflow. The chosen header layout must have a fixed, explicitly documented number of words for each command, so early and missing header TLAST can be detected deterministically.

## Errors

Errors go in AXI-Lite status registers (halted, error code, tx_id). On an error the kernel closes any output burst it started with TLAST and returns without reading more input. Nothing persists between activations: the status registers are cleared at the start of each one.

Every command header must contain exactly the number of words required by the chosen header layout, with TLAST asserted only on the final header word.

The following errors are checked in priority order:

1. EARLY_HEADER_TLAST: TLAST is asserted before the final required command-header word. Stop immediately, consume no additional input, and emit no response. Report the tx_id if it has already been received; otherwise report tx_id=0.
2. MISSING_HEADER_TLAST: TLAST is not asserted on the final required command-header word. Stop immediately without consuming additional words or sample data. Emit no response and report the received tx_id, or 0 if unavailable.
3. INVALID_COMMAND: The command code is unknown. Report the offending tx_id, emit no response, and stop without reading sample data.
4. INVALID_PARAMETERS: N=0, N>256, W=0, or W>8. Report the offending tx_id, emit no response, and stop without reading sample data.
5. EARLY_TLAST: The DATA burst asserts TLAST before the Nth sample. Output only maxima from complete windows strictly preceding the offending sample. Discard the unfinished window and do not emit its maximum.
6. MISSING_TLAST: The DATA burst does not assert TLAST on the Nth sample. Do not consume an additional sample. Retain only maxima from complete windows strictly before the Nth sample; discard the final window.

Header framing errors take priority over command decoding and parameter validation. Command decoding takes priority over parameter validation. Data framing errors are evaluated only after successful header and parameter validation. For mutually exclusive data framing errors, report the error observed first.

For an error during DATA processing, a response header may already have been emitted. Close any started output result burst correctly with TLAST. If no results were produced, do not start an empty result burst. Do not emit any further response headers or a footer after an error.

After any error, set halted=1, the corresponding error code, and the failing tx_id. The kernel returns without reading additional input. The host resets the stream path before starting a fresh activation.

On successful END, halted=0 and error code=0.

## Verification

Write a bit-exact Python model and independently verify it using hand-computed examples and NumPy array slices with max().

Hand-computed examples:

- N=5, W=2, [3, 7, -2, 9, 4] -> [7, 9, 4].
- N=4, W=2, [-8, -3, -10, -1] -> [-3, -1].
- N=3, W=8, [5, 1, 7] -> [7].
- N=4, W=1, [2, -1, 0, 6] -> [2, -1, 0, 6].

Verify signed comparisons, including INT32_MIN and INT32_MAX.

Required scenarios:

- Minimum N=1 and W=1.
- Maximum N=256 and W=8.
- N not divisible by W.
- W greater than N.
- All-negative samples, repeated values, and signed integer extremes.
- Multiple DATA commands with different N and W in one activation, followed by END.
- Unknown command, invalid N, invalid W, early data TLAST, and missing data TLAST.
- Early command-header TLAST, including a header truncated before tx_id is available.
- Missing command-header TLAST on the required final header word.
- Each error activation followed by a fresh successful activation, verifying cleared status and correct results.

For both header-framing error cases, verify the correct error priority, tx_id reporting, no additional input consumption, and no response. Follow each error activation with a fresh successful activation to verify status reset.

Check that unknown commands and invalid parameters do not consume sample bursts. Check exact output values, response tx_id, output counts, TLAST positions, and AXI-Lite status.

Compare the model, C simulation and co-simulation bit for bit. Run co-simulation once: one testbench run that walks through every scenario in order, calling the kernel once per activation with fresh streams, including each error activation and the fresh activation after it.

## results.md

Write results.md with these sections:

- Interface: the command and response layouts, how values pack into words, the status registers and the error codes.
- Design: the loop structure, where state is kept, and how the throughput target is reached.
- Targets: each target above with the required value, the measured value, PASS/FAIL and the file it came from (co-simulation for timing; synthesis for clock, II and resources). Always report the latency and throughput from co-simulation and the estimated clock and resources from synthesis, writing "no target" where this prompt sets none.
- Verification: the model's check against the hand-computed examples and the reference; then one row per scenario saying whether the model, C simulation and co-simulation agree, and for each error scenario the status it set and whether the fresh activation after it passed. Name the source file of each.
- Assumptions: every place this prompt was silent or ambiguous and what you chose, apart from interface details already given under Interface.
- Open Items: questions you could not settle, and anything that failed or was not done, or "None".
- Run History: one row per run, with the prompt version, what was built or changed, and what passed, so that a later run can continue from here.

Every number and PASS/FAIL in Targets and Verification names the file it came from (a log, a comparison output or a synthesis report), and that file stays in this folder to be committed: a reviewer opens them to check results.md.

## Revision history

| Version | Change | Why |
| --- | --- | --- |
| 1 | First version | Initial 1-D max pooling specification for Part 3. |
| 2 | Defined header TLAST errors and priority; restricted stream width to 32 bits; added verification cases | Address Part 3 autograder feedback. |