// See LICENSE for license details.

#include "bridges/loadmem_bin.h"

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <vector>

#include "core/simif.h"
#include "plusarg.h"

namespace {

static uint64_t parse_u64(const std::string &s, const std::string &flag_name) {
  if (s.empty()) {
    fprintf(stderr, "[loadmembin] empty value for +%s\n", flag_name.c_str());
    std::exit(EXIT_FAILURE);
  }
  errno = 0;
  char *end = nullptr;
  // base 0 = auto-detect 0x / 0 / decimal
  unsigned long long v = std::strtoull(s.c_str(), &end, 0);
  if (errno != 0 || end == s.c_str() || *end != '\0') {
    fprintf(stderr,
            "[loadmembin] cannot parse value '%s' for +%s as integer\n",
            s.c_str(),
            flag_name.c_str());
    std::exit(EXIT_FAILURE);
  }
  return static_cast<uint64_t>(v);
}

} // namespace

loadmem_bin_t::loadmem_bin_t(loadmem_t &load_mem,
                             const std::vector<std::string> &args)
                             : loadmem(load_mem) {
  std::string base_str = find_plusarg(args, "baseaddress");
  if (!base_str.empty()) {
    base_address = parse_u64(base_str, "baseaddress");
  }
  bin_filename = find_plusarg(args, "loadmembin");
}

void loadmem_bin_t::load_mem_from_bin_file() {
  if (bin_filename.empty()) {
    fprintf(stderr,
            "[loadmembin] load_mem_from_bin_file() called but "
            "+loadmembin was not provided\n");
    std::exit(EXIT_FAILURE);
  }
  load_mem_from_bin_file(bin_filename, base_address);
}

void loadmem_bin_t::load_mem_from_bin_file(const std::string &filename,
                                           uint64_t base_addr) {
  fprintf(stdout,
          "[loadmembin] start loading binary file: %s at 0x%016lx\n",
          filename.c_str(),
          (unsigned long)base_addr);

  std::ifstream file(filename.c_str(), std::ios::binary);
  if (!file) {
    fprintf(stderr, "[loadmembin] cannot open %s\n", filename.c_str());
    std::exit(EXIT_FAILURE);
  }

  const unsigned chunk = loadmem.get_mem_data_chunk();              // 32-bit words/beat
  const size_t   chunk_bytes = chunk * sizeof(uint32_t);    // bytes per beat

  // Stream the file in chunk-sized buffers so we don't hold the whole binary
  // in memory at once. Each iteration emits one AXI beat of width chunk_bytes.
  std::vector<uint8_t> buf(chunk_bytes);
  uint64_t addr = base_addr;
  size_t   total_bytes = 0;

  mpz_t data;
  mpz_init(data);

  while (file) {
    file.read(reinterpret_cast<char *>(buf.data()), chunk_bytes);
    std::streamsize got = file.gcount();
    if (got <= 0) {
      break;
    }
    // Zero-pad the tail so the final beat is well-defined.
    if (static_cast<size_t>(got) < chunk_bytes) {
      std::memset(buf.data() + got, 0, chunk_bytes - got);
    }

    // Pack `chunk_bytes` of little-endian bytes into the mpz_t. Using
    // word-order = -1 (least-significant word first) and endian = -1 (LE
    // within each word) matches the wire format expected by write_mem_chunk,
    // which itself exports with order = -1, endian = 0.
    mpz_import(data,
               /*count=*/chunk_bytes,
               /*order=*/-1,
               /*size=*/1,
               /*endian=*/0,
               /*nails=*/0,
               buf.data());

    loadmem.write_mem_chunk(addr, data, chunk_bytes);
    addr        += chunk_bytes;
    total_bytes += static_cast<size_t>(got);
  }

  mpz_clear(data);
  file.close();

  fprintf(stdout,
          "[loadmembin] done; loaded %zu bytes (%.2f MiB), end addr 0x%016lx\n",
          total_bytes,
          total_bytes / (1024.0 * 1024.0),
          (unsigned long)addr);
}