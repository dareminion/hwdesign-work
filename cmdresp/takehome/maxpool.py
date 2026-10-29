"""Stage 1 schemas and bit-exact reference function for 1-D max pooling."""
from __future__ import annotations

from enum import IntEnum

import numpy as np
import numpy.typing as npt

from waveflow.hw.dataschema import DataList, EnumField, IntField


class Command(IntEnum):
    DATA = 0
    END = 1


class ErrorCode(IntEnum):
    NO_ERROR = 0
    EARLY_HEADER_TLAST = 1
    MISSING_HEADER_TLAST = 2
    INVALID_COMMAND = 3
    INVALID_PARAMETERS = 4
    EARLY_TLAST = 5
    MISSING_TLAST = 6


UInt32 = IntField.specialize(bitwidth=32, signed=False)
Int32 = IntField.specialize(bitwidth=32, signed=True)
CommandField = UInt32
ErrorCodeField = EnumField.specialize(enum_type=ErrorCode, bitwidth=32)


class PoolCmdHdr(DataList):
    """Four-word command header; TLAST belongs only on its fourth word."""

    elements = {
        "cmd_type": {"schema": CommandField, "description": "DATA or END"},
        "tx_id": {"schema": UInt32, "description": "Transaction identifier"},
        "n": {"schema": UInt32, "description": "Number of input samples"},
        "window": {"schema": UInt32, "description": "Pooling window size"},
    }


class PoolRespHdr(DataList):
    """Response header with transaction ID and number of output values."""

    elements = {
        "tx_id": {"schema": UInt32, "description": "Echoed transaction identifier"},
        "output_count": {"schema": UInt32, "description": "Number of pooled values"},
    }


SCHEMA_CLASSES = [CommandField, ErrorCodeField, PoolCmdHdr, PoolRespHdr]


def maxpool_eval(samples: npt.ArrayLike, window: int) -> npt.NDArray[np.int32]:
    """Return exact signed-int32 maxima from consecutive, non-overlapping windows."""
    values = np.asarray(samples, dtype=np.int64)
    if values.ndim != 1:
        raise ValueError("samples must be one-dimensional")
    if values.size < 1 or values.size > 256:
        raise ValueError("sample count must be in [1, 256]")
    if window < 1 or window > 8:
        raise ValueError("window must be in [1, 8]")
    if np.any(values < -(1 << 31)) or np.any(values > (1 << 31) - 1):
        raise ValueError("samples must fit signed 32-bit integers")

    count = (values.size + window - 1) // window
    output = np.empty(count, dtype=np.int32)
    for index in range(count):
        start = index * window
        output[index] = np.max(values[start:min(start + window, values.size)])
    return output