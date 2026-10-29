#include "gen/maxpool.hpp"
#include "include/bundle_tb.h"

#include <cstdio>
#include <fstream>
#include <string>

int main(int argc, char** argv) {
    if (argc != 3) {
        std::fprintf(stderr, "usage: maxpool_tb <data_dir> <stage>\n");
        return 2;
    }
    const std::string root = argv[1];
    const std::string stage = argv[2];
    std::ifstream list(root + "/scenarios.txt");
    if (!list) {
        std::fprintf(stderr, "cannot open scenario list under %s\n", root.c_str());
        return 2;
    }

    std::string name;
    int run = 0;
    while (list >> name) {
        const std::string directory = root + "/" + name;
        ap_uint<1> halted = 1;
        ap_uint<8> error = static_cast<unsigned int>(MaxpoolError::MISSING_TLAST);
        ap_uint<32> tx_id = 0xFFFFFFFF;

        hls::stream<streamutils::axi4s_word<32>> s_in;
        hls::stream<streamutils::axi4s_word<32>> m_out;
        wf::play_stream<32>(directory + "/in", s_in);
        maxpool(s_in, m_out, halted, error, tx_id);

        wf::record_stream<32>(m_out, directory + "/" + stage);
        std::ofstream status(directory + "/" + stage + "/status.json");
        status << "{\"halted\": " << static_cast<int>(halted)
               << ", \"error\": " << static_cast<int>(error)
               << ", \"tx_id\": " << static_cast<unsigned int>(tx_id) << "}\n";

        unsigned int unread = 0;
        while (!s_in.empty()) {
            s_in.read();
            ++unread;
        }
        std::ofstream unread_file(directory + "/" + stage + "/unread_words.txt");
        unread_file << unread << "\n";
        std::printf("scenario %-42s halted=%d error=%d tx_id=%u unread_words=%u\n",
                    name.c_str(), static_cast<int>(halted), static_cast<int>(error),
                    static_cast<unsigned int>(tx_id), unread);
        ++run;
    }
    if (run == 0) {
        std::fprintf(stderr, "no scenarios ran from %s\n", root.c_str());
        return 1;
    }
    std::printf("completed %d fresh-stream activations\n", run);
    return 0;
}