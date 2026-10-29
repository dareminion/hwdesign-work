#ifndef INCLUDE_MAXPOOL_ERROR_TB_H
#define INCLUDE_MAXPOOL_ERROR_TB_H

#include <cctype>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include "streamutils_tb.h"

#define WAVEFLOW_ENABLE_MAXPOOL_ERROR_TB_H_MEMBERS
#include "maxpool_error.h"
#undef WAVEFLOW_ENABLE_MAXPOOL_ERROR_TB_H_MEMBERS

inline const char* enum_to_string(MaxpoolError value) {
    switch (value) {
    case MaxpoolError::NO_ERROR:
        return "NO_ERROR";
    case MaxpoolError::EARLY_HEADER_TLAST:
        return "EARLY_HEADER_TLAST";
    case MaxpoolError::MISSING_HEADER_TLAST:
        return "MISSING_HEADER_TLAST";
    case MaxpoolError::INVALID_COMMAND:
        return "INVALID_COMMAND";
    case MaxpoolError::INVALID_PARAMETERS:
        return "INVALID_PARAMETERS";
    case MaxpoolError::EARLY_TLAST:
        return "EARLY_TLAST";
    case MaxpoolError::MISSING_TLAST:
        return "MISSING_TLAST";
    default:
        return "UNKNOWN";
    }
}

#endif // INCLUDE_MAXPOOL_ERROR_TB_H