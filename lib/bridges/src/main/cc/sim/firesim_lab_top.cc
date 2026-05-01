
// =============================================================================
//  firesim_lab_top.cc
//  The top level firesim-lab driver that drives the simulation.
// =============================================================================
#include "core/simulation.h"
#include "core/systematic_scheduler.h"
#include "bridges/peek_poke.h"
#include "bridges/loadmem_bin.h"
#include "bridges/loadmem.h"
#include "plusarg.h"
#include "sim/firesim_lab_top.h"
#include <memory>
#include <vector>
#include <string>

firesim_lab_top_t::firesim_lab_top_t(simif_t &simif, widget_registry_t &registry,
                    const std::vector<std::string> &args)
                : systematic_scheduler_t(args),
                  simulation_t(registry, args),
                  simif(simif),
                  peek_poke(registry.get_widget<peek_poke_t>()) {

    auto *loadmem = registry.get_widget_opt<loadmem_t>();

    std::string bin_path = find_plusarg(args, "loadmembin");
    std::string hex_path = find_plusarg(args, "loadmem");
    if (!bin_path.empty() && !hex_path.empty()) {
        fprintf(stderr,
                "firesim-lab: +loadmem and +loadmembin are mutually exclusive\n");
        std::exit(EXIT_FAILURE);
    }

   if(!bin_path.empty()){
        binloader.emplace(*loadmem, args);
   }
}