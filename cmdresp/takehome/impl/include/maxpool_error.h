#ifndef INCLUDE_MAXPOOL_ERROR_H
#define INCLUDE_MAXPOOL_ERROR_H

#include <ap_int.h>
#include <hls_stream.h>
#if __has_include(<hls_axi_stream.h>)
#include <hls_axi_stream.h>
#else
#include <ap_axi_sdata.h>
#endif
#include "streamutils_hls.h"

enum class MaxpoolError {
    NO_ERROR = 0,
    EARLY_HEADER_TLAST = 1,
    MISSING_HEADER_TLAST = 2,
    INVALID_COMMAND = 3,
    INVALID_PARAMETERS = 4,
    EARLY_TLAST = 5,
    MISSING_TLAST = 6,
};

#endif // INCLUDE_MAXPOOL_ERROR_H