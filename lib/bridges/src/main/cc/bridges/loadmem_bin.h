// See LICENSE for license details.

#ifndef __LOADMEM_BIN_H
#define __LOADMEM_BIN_H

#include <cstdint>
#include <string>

#include "bridges/loadmem.h"

// Extended loadmem widget that can also load a flat binary file into DRAM.
//
// Flat binary loading is requested at runtime via plusargs. The constructor
// reads them out of `args`:
//
//   +loadmembin=<path>            file to load (required to enable bin mode)
//   +baseaddress=<addr>           target DRAM byte address; default 0x80000000.
//                                 Accepts decimal or 0x-prefixed hex.
//
// The hex-format loader inherited from `loadmem_t` (i.e. +loadmem=) remains
// fully functional. It is an error to set both +loadmem and +loadmembin in the
// same run; the simulation driver should detect that before calling
// load_mem_from_bin_file().
class loadmem_bin_t {
public:

  loadmem_bin_t(loadmem_t &loadmem, const std::vector<std::string> &args);

  // True if +loadmembin=<path> was supplied.
  bool has_bin_file() const { return !bin_filename.empty(); }

  const std::string &get_bin_filename() const { return bin_filename; }
  uint64_t get_base_address() const { return base_address; }

  // Loads the contents of `filename` as a flat binary starting at
  // `base_address`. Pads the final transfer with zeros to a chunk boundary.
  void load_mem_from_bin_file(const std::string &filename, uint64_t base_addr);

  // Convenience wrapper: load the file/address parsed from plusargs.
  void load_mem_from_bin_file();

private:
  std::string bin_filename;
  uint64_t base_address;
  loadmem_t loadmem;
};

#endif // __LOADMEM_BIN_H