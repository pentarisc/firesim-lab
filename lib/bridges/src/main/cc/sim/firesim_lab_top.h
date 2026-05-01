#include "core/simulation.h"
#include "core/systematic_scheduler.h"
#include "bridges/peek_poke.h"
#include "bridges/loadmem_bin.h"
#include <vector>
#include <string>

class firesim_lab_top_t : public systematic_scheduler_t, public simulation_t {
public:
  firesim_lab_top_t(simif_t &simif,
                    widget_registry_t &registry,
                    const std::vector<std::string> &args);

    bool simulation_timed_out() override { return !terminated; }

    int simulation_run() override { return 0; }

    void simulation_init() override {
        // call parent init first.
        simulation_t::simulation_init();

        // In simulation_t::execute_simulation_flow(), init_dram() (private function)
        // is called first. and then simulation_init() is called. We hook into this
        // function and load binary. Since the above constructor makes sure that
        // both +loadmem and +loadmembin can not co-exist in the arguments, we can
        // be sure that only one of loadmem or loadmembin is invoked, because binloader
        // will be instantiated if both are set and only if loadmembin is set.
        if (binloader.has_value()) {
            binloader.value().load_mem_from_bin_file();
        }
    }

private:
  
    simif_t &simif;
    /// Reference to the peek-poke bridge.
    peek_poke_t &peek_poke;
    /// Flag to indicate that the simulation was terminated.
    bool terminated = false;

    std::optional<loadmem_bin_t> binloader;
};