#include "include/header_word.h"
#include "include/maxpool_cmd_hdr.h"
#include "include/maxpool_error.h"
#include "include/maxpool_resp_hdr.h"
#include "int32_array_utils.h"

namespace maxpool_impl {

static inline ap_uint<8> error_code(MaxpoolError value) {
#pragma HLS INLINE
    return static_cast<unsigned int>(value);
}

static inline void write_result(
        hls::stream<streamutils::axi4s_word<32>>& m_out,
        int32_array_utils::value_type value,
        bool tlast) {
#pragma HLS INLINE
    int32_array_utils::value_type lane[1];
    lane[0] = value;
    int32_array_utils::write_axi4_stream_lane<32>(lane, m_out, tlast, 1);
}

template <int in_bw, int out_bw>
void body(hls::stream<streamutils::axi4s_word<in_bw>>& s_in,
          hls::stream<streamutils::axi4s_word<out_bw>>& m_out,
          ap_uint<1>& halted,
          ap_uint<8>& error,
          ap_uint<32>& tx_id) {
#pragma HLS INLINE
    static_assert(in_bw == 32 && out_bw == 32, "maxpool requires 32-bit streams");
    halted = 0;
    error = error_code(MaxpoolError::NO_ERROR);
    tx_id = 0;

    while (true) {
        MaxpoolCmdHdr command;
        ap_uint<32> header_values[4];
        streamutils::tlast_status header_status = streamutils::tlast_status::no_tlast;
        ap_uint<32> received_tx_id = 0;

        for (int word_index = 0; word_index < 4; ++word_index) {
#pragma HLS PIPELINE II=1
            HeaderWord header_word;
            header_word.read_axi4_stream<32>(s_in, header_status);
            header_values[word_index] = header_word.value;
            if (word_index == 1) {
                received_tx_id = header_word.value;
            }
            if (header_status == streamutils::tlast_status::tlast_at_end && word_index < 3) {
                halted = 1;
                error = error_code(MaxpoolError::EARLY_HEADER_TLAST);
                tx_id = received_tx_id;
                return;
            }
        }

        command.cmd_type = header_values[0];
        command.tx_id = header_values[1];
        command.n = header_values[2];
        command.window = header_values[3];

        if (header_status != streamutils::tlast_status::tlast_at_end) {
            halted = 1;
            error = error_code(MaxpoolError::MISSING_HEADER_TLAST);
            tx_id = received_tx_id;
            return;
        }
        if (command.cmd_type == 1) {
            return;
        }
        if (command.cmd_type != 0) {
            halted = 1;
            error = error_code(MaxpoolError::INVALID_COMMAND);
            tx_id = command.tx_id;
            return;
        }
        if (command.n == 0 || command.n > 256 || command.window == 0 || command.window > 8) {
            halted = 1;
            error = error_code(MaxpoolError::INVALID_PARAMETERS);
            tx_id = command.tx_id;
            return;
        }

        MaxpoolRespHdr response;
        response.tx_id = command.tx_id;
        response.output_count = (command.n + command.window - 1) / command.window;
        response.write_axi4_stream<32>(m_out, true);

        bool current_valid = false;
        bool pending_valid = false;
        int32_array_utils::value_type current_max = 0;
        int32_array_utils::value_type pending_max = 0;
        int samples_in_window = 0;
        MaxpoolError failure = MaxpoolError::NO_ERROR;

        for (int sample_index = 0; sample_index < command.n; ++sample_index) {
#pragma HLS PIPELINE II=1
            int32_array_utils::value_type sample_lane[1];
            streamutils::tlast_status sample_status = streamutils::tlast_status::no_tlast;
            int32_array_utils::read_axi4_stream_lane<32>(s_in, sample_lane, 1, sample_status);

            const bool early = sample_status == streamutils::tlast_status::tlast_at_end
                            && sample_index < command.n - 1;
            const bool missing = sample_index == command.n - 1
                              && sample_status != streamutils::tlast_status::tlast_at_end;
            if (early || missing) {
                failure = early ? MaxpoolError::EARLY_TLAST : MaxpoolError::MISSING_TLAST;
                if (pending_valid) {
                    write_result(m_out, pending_max, true);
                }
                halted = 1;
                error = error_code(failure);
                tx_id = command.tx_id;
                return;
            }

            const int32_array_utils::value_type sample = sample_lane[0];
            if (!current_valid || sample > current_max) {
                current_max = sample;
                current_valid = true;
            }
            ++samples_in_window;

            const bool window_complete = samples_in_window == command.window;
            const bool final_sample = sample_index == command.n - 1;
            if (window_complete || final_sample) {
                if (pending_valid) {
                    write_result(m_out, pending_max, false);
                }
                pending_max = current_max;
                pending_valid = true;
                current_valid = false;
                samples_in_window = 0;
            }
        }

        if (pending_valid) {
            write_result(m_out, pending_max, true);
        }
    }
}

}  // namespace maxpool_impl