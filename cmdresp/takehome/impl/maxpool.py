"""maxpool.py -- the streaming polynomial accelerator, written hook-first.

What is in this file, in the order a reader needs it:

1. **Schemas** -- the command header (which carries the coefficients), the response header
   and the error codes.  Waveflow generates their C++ headers and serializers; nothing packs
   words by hand.
2. **The bit-exact model** -- pure functions with no simulator in them:
   :func:`maxpool_eval` (the arithmetic, float32 in the C++ operation order) and
   :func:`maxpool_stream_model` (the whole protocol over a stream of bursts, TLAST rules
   included).  This is what a system simulation calls, and what the C++ kernel is
   checked against, byte for byte.
3. **The module** -- :class:`MaxpoolAccel` declares the ports and the register map (status
   only) and names its kernel body (``cpp_body = "body"``).  Waveflow generates the
   kernel's boundary (the prototype and every interface pragma) and a single call to the
   hand-written C++ body, ``maxpool_body_impl.tpp``.  The Python :meth:`MaxpoolAccel.body` is the same kernel for
   pysim: a thin port wrapper around :func:`maxpool_eval`, plus a timing model.
4. **The pysim testbench** -- :class:`MaxpoolTB` plays a scenario's stimulus file into the
   module, the same file the hand-written C++ testbench (``maxpool_tb.cpp``) plays into the
   Vitis kernel.

The scenarios, their independently computed expected outputs, and the checker are in
``scenarios.py``; the build that runs everything is ``maxpool_build.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import ClassVar

import numpy as np
import numpy.typing as npt

from waveflow.hw.arrayutils import array, read_array, write_array
from waveflow.hw.clock import Clock
from waveflow.hw.dataschema import DataList, EnumField, IntField
from waveflow.hw.hw_hostactivated import HostActivated
from waveflow.hw.hw_module import HwParam
from waveflow.hw.interface import StreamIF, StreamIFMaster, StreamIFSlave
from waveflow.hw.irq import IrqIF, IrqIFSink
from waveflow.hw.memif import DirectMMIF, MMIFMaster
from waveflow.hw.regmap import (
    Bit,
    RegAccess,
    RegField,
    VitisRegMap,
    VitisRegMapMMIFSlave,
)
from waveflow.simulation.logger import Logger, NullLogger
from waveflow.simulation.simobj import ProcessGen, SimObj
from waveflow.simulation.simulation import Simulation
from waveflow.utils.burst_io import StreamBurst, read_bursts

# ---------------------------------------------------------------------------
# 1. Schemas
# ---------------------------------------------------------------------------

INCLUDE_DIR = "include"
WORD_BW_SUPPORTED = [32]
UInt32 = IntField.specialize(bitwidth=32, signed=False)
Int32 = IntField.specialize(bitwidth=32, signed=True)


class MaxpoolError(IntEnum):
    """What the kernel reports in its ``error`` register when it halts."""
    NO_ERROR = 0
    EARLY_HEADER_TLAST = 1
    MISSING_HEADER_TLAST = 2
    INVALID_COMMAND = 3
    INVALID_PARAMETERS = 4
    EARLY_TLAST = 5
    MISSING_TLAST = 6

MaxpoolErrorField = EnumField.specialize(enum_type=MaxpoolError)


class MaxpoolCmdType(IntEnum):
    DATA = 0
    END = 1

MaxpoolCmdTypeField = UInt32


class HeaderWord(DataList):
    """One schema-serialized 32-bit word, used to validate TLAST between fields."""

    elements = {"value": {"schema": UInt32, "description": "One header word"}}


class MaxpoolCmdHdr(DataList):
    """Four-word command header: command, tx_id, N, W."""
    elements = {
        "cmd_type": {"schema": UInt32, "description": "DATA=0 or END=1"},
        "tx_id": {"schema": UInt32, "description": "Transaction identifier"},
        "n": {"schema": UInt32, "description": "Input sample count"},
        "window": {"schema": UInt32, "description": "Pooling window size"},
    }


class MaxpoolRespHdr(DataList):
    """Response header: tx_id and output count."""
    elements = {
        "tx_id": {"schema": UInt32, "description": "Echo of transaction ID"},
        "output_count": {"schema": UInt32, "description": "Number of result values"},
    }


SCHEMA_CLASSES = [
    MaxpoolErrorField,
    HeaderWord,
    MaxpoolCmdHdr,
    MaxpoolRespHdr,
]

# ---------------------------------------------------------------------------
# 2. The bit-exact model (pure functions)
# ---------------------------------------------------------------------------


def maxpool_eval(samples: npt.ArrayLike, window: int) -> npt.NDArray[np.int32]:
    """Exact signed-int32 maxima from consecutive, non-overlapping windows."""
    values = np.asarray(samples, dtype=np.int64)
    if values.ndim != 1 or not 1 <= values.size <= 256:
        raise ValueError("sample count must be in [1, 256]")
    if not 1 <= int(window) <= 8:
        raise ValueError("window must be in [1, 8]")
    if np.any(values < -(1 << 31)) or np.any(values > (1 << 31) - 1):
        raise ValueError("samples must fit signed int32")
    count = (values.size + int(window) - 1) // int(window)
    result = np.empty(count, dtype=np.int32)
    for index in range(count):
        start = index * int(window)
        result[index] = np.max(values[start:min(start + int(window), values.size)])
    return result


@dataclass
class MaxpoolStreamResult:
    """What the kernel does to one stream: its output bursts and its final register status."""

    out: list[StreamBurst]
    halted: int = 0
    error: int = int(MaxpoolError.NO_ERROR)
    tx_id: int = 0
    consumed_words: int = 0

    def status(self) -> dict[str, int]:
        return {"halted": self.halted, "error": self.error, "tx_id": self.tx_id}


def samples_per_word(word_bw: int) -> int:
    """Signed int32 samples per required 32-bit word."""
    if word_bw != 32:
        raise ValueError(f"word_bw must be 32, got {word_bw}")
    return 1


def maxpool_stream_model(bursts: list[StreamBurst], word_bw: int = 32) -> MaxpoolStreamResult:
    """Pure bit-exact command-response model with header and sample TLAST checks."""
    if word_bw != 32:
        raise ValueError(f"word_bw must be 32, got {word_bw}")
    words = [(int(w), i + 1 == len(b.words) and b.tlast)
             for b in bursts for i, w in enumerate(np.asarray(b.words, dtype=np.uint64))]
    hdr_words = MaxpoolCmdHdr().serialize(word_bw=32).size
    result = MaxpoolStreamResult(out=[])
    output_words: list[int] = []
    pos = 0

    def halt(code: MaxpoolError, tx: int) -> None:
        result.halted, result.error, result.tx_id = 1, int(code), int(tx)

    def emit_header(tx: int, count: int) -> None:
        header = MaxpoolRespHdr()
        header.tx_id, header.output_count = tx, count
        result.out.append(StreamBurst(np.asarray(header.serialize(32), dtype=np.uint64)))

    def close_results() -> None:
        if output_words:
            result.out.append(StreamBurst(write_array(output_words, elem_type=Int32, word_bw=32)))
            output_words.clear()

    def finish() -> MaxpoolStreamResult:
        result.consumed_words = pos
        return result

    while True:
        header_values: list[int] = []
        header_tlast: list[bool] = []
        tx_id = 0
        for index in range(hdr_words):
            if pos >= len(words):
                raise ValueError("stimulus ends inside a command header")
            value, last = words[pos]
            pos += 1
            header_values.append(value)
            header_tlast.append(last)
            if index == 1:
                tx_id = value
            if last and index < hdr_words - 1:
                halt(MaxpoolError.EARLY_HEADER_TLAST, tx_id)
                return finish()
        if not header_tlast[-1]:
            halt(MaxpoolError.MISSING_HEADER_TLAST, tx_id)
            return finish()

        header = MaxpoolCmdHdr().deserialize(header_values, word_bw=32)
        if int(header.cmd_type) == int(MaxpoolCmdType.END):
            return finish()
        if int(header.cmd_type) != int(MaxpoolCmdType.DATA):
            halt(MaxpoolError.INVALID_COMMAND, int(header.tx_id))
            return finish()
        n, window = int(header.n), int(header.window)
        if not 1 <= n <= 256 or not 1 <= window <= 8:
            halt(MaxpoolError.INVALID_PARAMETERS, int(header.tx_id))
            return finish()

        output_count = (n + window - 1) // window
        emit_header(int(header.tx_id), output_count)
        pending_valid = False
        pending_max = 0
        current_valid = False
        current_max = 0
        error = MaxpoolError.NO_ERROR
        for sample_index in range(n):
            if pos >= len(words):
                raise ValueError("stimulus ends inside a sample burst")
            raw, last = words[pos]
            pos += 1
            sample = raw if raw < (1 << 31) else raw - (1 << 32)
            early = last and sample_index < n - 1
            missing = sample_index == n - 1 and not last
            if early or missing:
                error = MaxpoolError.EARLY_TLAST if early else MaxpoolError.MISSING_TLAST
                if pending_valid:
                    output_words.append(pending_max)
                close_results()
                halt(error, int(header.tx_id))
                return finish()

            if not current_valid or sample > current_max:
                current_valid, current_max = True, sample
            window_done = ((sample_index + 1) % window == 0) or sample_index == n - 1
            if window_done:
                if pending_valid:
                    output_words.append(pending_max)
                pending_valid, pending_max = True, current_max
                current_valid = False
        if pending_valid:
            output_words.append(pending_max)
        close_results()


# ---------------------------------------------------------------------------
# 3. The module: ports, register map, and the kernel body
# ---------------------------------------------------------------------------


@dataclass
class MaxpoolAccel(HostActivated):
    """The accepted 32-bit stream-inband accelerator interface."""

    cpp_kernel_name: ClassVar[str | None] = "maxpool"
    # The hook-wrapping namespace must differ from the kernel function name, or
    # `::maxpool(...)` at the testbench call site is ambiguous.
    cpp_namespace:   ClassVar[str | None] = "maxpool_impl"
    cpp_body:        ClassVar[str | None] = "body"
    param_supports:  ClassVar[dict | None] = None

    in_bw:        HwParam[int] = 32
    out_bw:       HwParam[int] = 32
    aximm_bw:     HwParam[int] = 32
    clk:          Clock = field(default_factory=lambda: Clock(freq=1e9))
    logger:       Logger | NullLogger = field(default_factory=NullLogger)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.s_in  = StreamIFSlave( name=f'{self.name}_s_in',  sim=self.sim, bitwidth=self.in_bw)
        self.m_out = StreamIFMaster(name=f'{self.name}_m_out', sim=self.sim, bitwidth=self.out_bw)
        self.regmap = VitisRegMap({
            "halted": RegField(Bit,            RegAccess.R, description="1 = halted on error"),
            "error":  RegField(MaxpoolErrorField, RegAccess.R, description="Error code (MaxpoolError)"),
            "tx_id":  RegField(UInt32,          RegAccess.R, description="tx_id of the failed command"),
        }, bitwidth=self.aximm_bw)
        self.s_lite = VitisRegMapMMIFSlave(
            name=f'{self.name}_s_lite', sim=self.sim, bitwidth=self.aximm_bw,
            regmap=self.regmap, on_start=self.on_start,
        )
        for ep in (self.s_in, self.m_out, self.s_lite):
            self.add_endpoint(ep)

    def _halt(self, err: MaxpoolError, tx_id: int) -> None:
        self.regmap.set("error", err)
        self.regmap.set("tx_id", tx_id)
        self.regmap.set("halted", 1)

    def body(self) -> ProcessGen[None]:
        """Functional Python body; no software timing estimate is declared."""
        for f in ("halted", "error", "tx_id"):
            self.regmap.set(f, 0)
        while True:
            cmd_hdr: MaxpoolCmdHdr = yield from self.s_in.get_schema(MaxpoolCmdHdr)
            if int(cmd_hdr.cmd_type) == int(MaxpoolCmdType.END):
                return
            if int(cmd_hdr.cmd_type) != int(MaxpoolCmdType.DATA):
                self._halt(MaxpoolError.INVALID_COMMAND, int(cmd_hdr.tx_id))
                return
            n, window = int(cmd_hdr.n), int(cmd_hdr.window)
            if not 1 <= n <= 256 or not 1 <= window <= 8:
                self._halt(MaxpoolError.INVALID_PARAMETERS, int(cmd_hdr.tx_id))
                return
            resp_hdr = MaxpoolRespHdr()
            resp_hdr.tx_id = cmd_hdr.tx_id
            resp_hdr.output_count = (n + window - 1) // window
            yield from self.m_out.write(resp_hdr)
            samples = yield from self.s_in.get_array(Int32, n)
            results = maxpool_eval(samples.val, window)
            yield from self.m_out.write_array(Int32, len(results), data=array(Int32, results))


# ---------------------------------------------------------------------------
# 4. The pysim testbench
# ---------------------------------------------------------------------------

#: What the status registers hold before a run: what a kernel that forgot to clear its
#: status (rule 5) would leave behind.  Both testbenches poison them, so that kernel fails.
POISONED_STATUS = {"halted": 1, "error": int(MaxpoolError.MISSING_TLAST), "tx_id": 0xFFFF}


@dataclass(kw_only=True)
class MaxpoolTB(SimObj):
    """Plays a scenario's stimulus file into :class:`MaxpoolAccel` and records the response.

    The stimulus is the same burst bundle the C++ testbench plays (``<scenario>/in``).  One
    kernel run: the testbench poisons the status registers, starts the kernel and sleeps
    until its ``ap_done`` interrupt, while two other processes push the stimulus and collect
    the response -- pushing everything first could deadlock, because the kernel blocks writing
    output nobody is reading yet.  Then it reads the status.  After an error, whatever the
    kernel left unread stays in the input channel and the scenario ends: that is the host
    resetting the stream path (rule 7).  A pysim stream cannot omit TLAST, so the
    missing-TLAST scenario runs only through the pure model and the C++ kernel.
    """

    stimulus:  Path
    n_out:     int                       # output bursts to collect
    word_bw:   int = 32
    base_addr: int = 0x0

    def __post_init__(self) -> None:
        super().__post_init__()
        self.m_in   = StreamIFMaster(name=f'{self.name}_m_in',   sim=self.sim, bitwidth=self.word_bw)
        self.s_out  = StreamIFSlave( name=f'{self.name}_s_out',  sim=self.sim, bitwidth=self.word_bw)
        self.m_lite = MMIFMaster(    name=f'{self.name}_m_lite', sim=self.sim, bitwidth=32)
        self.irq    = IrqIFSink(     name=f'{self.name}_irq',    sim=self.sim)
        self.out: list[StreamBurst] = []
        self.status: dict[str, int] = {}
        self._regmap_ref: VitisRegMap | None = None

    def run_proc(self) -> ProcessGen[None]:
        for f, v in POISONED_STATUS.items():     # left over from "a previous run"
            self._regmap().set(f, v)
        rm = self._regmap().bind_master(self.m_lite, base_addr=self.base_addr)
        reader = self.env.process(self._read_all())
        self.env.process(self._write_all())
        yield from rm.run(self.irq)              # enable ap_done irq, start, sleep until done
        yield reader
        for f in ("halted", "error", "tx_id"):
            self.status[f] = int((yield from rm.get(f)))    # int: written as JSON

    def _write_all(self) -> ProcessGen[None]:
        word_t = np.uint32 if self.word_bw <= 32 else np.uint64
        for b in read_bursts(self.stimulus):
            yield from self.m_in.write(np.asarray(b.words, dtype=word_t))

    def _read_all(self) -> ProcessGen[None]:
        for _ in range(self.n_out):
            words = yield from self.s_out.get()
            self.out.append(StreamBurst(np.asarray(words, dtype=np.uint64), True))

    def _regmap(self) -> VitisRegMap:
        if self._regmap_ref is None:
            raise RuntimeError("MaxpoolTB._regmap_ref is unset; call connect() before run_sim().")
        return self._regmap_ref


def connect(sim: Simulation, tb: MaxpoolTB, accel: MaxpoolAccel, clk: Clock) -> None:
    """Wire the testbench to the accelerator: two streams, AXI-Lite, and the interrupt line."""
    in_stream  = StreamIF(sim=sim, clk=clk)
    out_stream = StreamIF(sim=sim, clk=clk)
    lite_link  = DirectMMIF(sim=sim, clk=clk, byte_addressable=True)
    irq        = IrqIF(name=f'{accel.name}_irq', sim=sim)
    in_stream.bind( "master", tb.m_in)
    in_stream.bind( "slave",  accel.s_in)
    out_stream.bind("master", accel.m_out)
    out_stream.bind("slave",  tb.s_out)
    lite_link.bind( "master", tb.m_lite)
    lite_link.bind( "slave",  accel.s_lite)
    irq.bind("source", accel.s_lite.interrupt())
    irq.bind("sink",   tb.irq)
    tb._regmap_ref = accel.regmap
