# Stage 1: Interface decisions and serialized layout

The chosen frame is Waveflow `stream_inband`, following `examples/stream_inband`.
All stream words are 32 bits. Header words are serialized with their Waveflow
`DataList` schemas; sample arrays use Waveflow's signed-int32 array utilities.

## Layout

The values below were produced by `PoolCmdHdr.serialize(32)`,
`PoolRespHdr.serialize(32)`, and `write_array(..., elem_type=Int32, word_bw=32)`
in `stage1_check.py`.

| Burst | Fixed word layout |
| --- | --- |
| DATA command header | word 0: unsigned command code (DATA=0); word 1: unsigned tx_id; word 2: N; word 3: W; TLAST on word 3 only |
| END command header | same four words, with END code and remaining values zero; TLAST on word 3 only |
| Sample burst | one signed two's-complement int32 sample per word; TLAST on sample N only |
| Response header | word 0: tx_id; word 1: ceil(N/W); TLAST on word 1 only |
| Result burst | one signed two's-complement int32 pooled value per word; TLAST on the final result |
| AXI-Lite | ap_start/ap_done and status only: halted, error, tx_id |

For `DATA`, tx_id 42, N=5, W=2, Waveflow serialized the command as
`0x00000000 0x0000002a 0x00000005 0x00000002`. The END header with zero unused
fields is `0x00000001 0x00000000 0x00000000 0x00000000`. The response with
tx_id 42 and output_count 3 is `0x0000002a 0x00000003`. Signed sample values
INT32_MIN and INT32_MAX serialize to `0x80000000` and `0x7fffffff`.

The fixed four-word command header allows early TLAST to be detected at every
position. `tx_id` is known after word 1; an early TLAST on word 0 therefore
reports tx_id 0, and later early-TLAST positions report the received tx_id.
The input and output stream widths are exactly 32 bits; no alternate-width
variant is required.

## Decisions

1. Parameters are carried in each DATA header. No configuration persists.
2. Each DATA produces one response header and one result burst; END produces no response.
3. N=0 is invalid; no data burst is read.
4. Error priority is header TLAST framing, command decoding, parameter validation,
   then sample TLAST framing, in the order specified by the take-home prompt.
5. Layout uses four whole 32-bit header words. Samples/results use signed int32
   schema/array serialization, never manual packing.
6. Arithmetic is exact signed comparison, with no rounding or saturation.
7. Status tx_id reports the failing command's ID, or zero when it has not yet been received.
8. Header TLAST must be asserted only on its fourth word. The DATA sample burst's
   TLAST must be asserted on its Nth word and nowhere earlier.
9. Command code is a 32-bit unsigned field so invalid values are representable in tests;
   code 0 is DATA, 1 is END, and every other value is invalid.
10. There is no response footer.

The prompt leaves the tx_id width unspecified; this design chooses unsigned 32-bit
tx_id so the fixed header uses one complete word per field. END carries zeros in
its unused fields. An error after a response header but before any result is
produced leaves the result burst absent; if results were already emitted, that
burst is closed with TLAST.