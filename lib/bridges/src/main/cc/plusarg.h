// See LICENSE for license details.

#ifndef __FIRESIM_LAB_PLUSARG_H
#define __FIRESIM_LAB_PLUSARG_H

#include <string>
#include <vector>

// Look for a plusarg of the form "+<name>=<value>" in `args` and return the
// <value> portion. Returns an empty string if the flag is absent.
//
// The leading '+' must NOT be included in `name`: pass "loadmembin", not
// "+loadmembin". This matches how plusargs are written on the command line
// while keeping callers from accidentally double-prefixing.
//
// If the same flag appears multiple times, the last occurrence wins. This is
// the conventional behavior for command-line flags and avoids surprising
// users who append an override at the end.
inline std::string find_plusarg(const std::vector<std::string> &args,
                                const std::string &name) {
  const std::string prefix = "+" + name + "=";
  std::string value;
  for (const auto &arg : args) {
    if (arg.compare(0, prefix.size(), prefix) == 0) {
      value = arg.substr(prefix.size());
    }
  }
  return value;
}

// Look for a bare flag of the form "+<name>" (no value) in `args`. Returns
// true if present. Useful for boolean switches like "+verbose".
//
// Does not match "+name=...". Use find_plusarg() for those.
inline bool has_plusarg(const std::vector<std::string> &args,
                        const std::string &name) {
  const std::string flag = "+" + name;
  for (const auto &arg : args) {
    if (arg == flag) {
      return true;
    }
  }
  return false;
}

#endif // __FIRESIM_LAB_PLUSARG_H