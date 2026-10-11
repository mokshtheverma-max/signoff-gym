# Signoff Gym sign-off checks (PLAN.md P0.4, P3.4).
#
# Run after the flow with:
#   make DESIGN_CONFIG=... run RUN_SCRIPT=<this file> RUN_LOG_NAME_STEM=sg_checks
# with SG_SIGNOFF_SDC set to the pristine SDC. Reads the flow's final
# database and prints the lines sg/metrics.py parse_checks reads: the
# period of every clock, and OpenSTA's list of unconstrained endpoints.

source $::env(SCRIPTS_DIR)/load.tcl
source_env_var_if_exists PLATFORM_TCL
source $::env(SCRIPTS_DIR)/read_liberty.tcl
read_db $::env(RESULTS_DIR)/6_final.odb
read_sdc $::env(SG_SIGNOFF_SDC)
source $::env(PLATFORM_DIR)/setRC.tcl

foreach sg_clk [all_clocks] {
  puts "SG_CLOCK [get_property $sg_clk name] [get_property $sg_clk period]"
}

sta::redirect_string_begin
check_setup -verbose -unconstrained_endpoints
set sg_text [sta::redirect_string_end]
puts "SG_UNCONSTRAINED_BEGIN"
puts $sg_text
puts "SG_UNCONSTRAINED_END"
