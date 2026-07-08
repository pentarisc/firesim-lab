#!/bin/bash

# This script is called by FireSim's bitbuilder to create a xclbin

# exit script if any command fails
set -e
set -o pipefail

usage() {
    echo "usage: ${0} [OPTIONS]"
    echo ""
    echo "Options"
    echo "   --cl_dir     : Custom logic directory to build AWS F1 bitstream from"
    echo "   --frequency  : Frequency in MHz of the desired FPGA host clock."
    echo "   --place      : (optional) Vivado place directive, forwarded as-is. Falls back to"
    echo "                  aws_build_dcp_from_cl.py's own default if omitted."
    echo "   --phy_opt    : (optional) Vivado physical-optimization directive, same fallback."
    echo "   --route      : (optional) Vivado route directive, same fallback."
    echo "   --extra_args : (optional) Verbatim extra args appended as-is to the aws_build_dcp_from_cl.py invocation."
    echo "   --help       : Display this message"
    exit "$1"
}

CL_DIR=""
FREQUENCY=""
PLACE=""
PHY_OPT=""
ROUTE=""
EXTRA_ARGS=""

# getopts does not support long options, and is inflexible
# ensure $1 arg is empty or else hdk_setup.sh will fail
while [ "$1" != "" ];
do
    case $1 in
        --help)
            usage 1 ;;
        --cl_dir )
            shift
            CL_DIR=$1 ;;
        --place )
            shift
            PLACE=$1 ;;
        --phy_opt )
            shift
            PHY_OPT=$1 ;;
        --route )
            shift
            ROUTE=$1 ;;
        --extra_args )
            shift
            EXTRA_ARGS=$1 ;;
        --frequency )
            shift
            FREQUENCY=$1 ;;
        * )
            echo "invalid option $1"
            usage 1 ;;
    esac
    shift
done

if [ -z "$CL_DIR" ] ; then
    echo "no cl directory specified"
    usage 1
fi

if [ -z "$FREQUENCY" ] ; then
    echo "No --frequency specified"
    usage 1
fi

AWS_FPGA_DIR=$CL_DIR/../../../..

# setup hdk # rh: -s is a flag that skips some git initialization on device, unnecessary as files already present on manager 
cd $AWS_FPGA_DIR
source hdk_setup.sh -s

export CL_DIR=$CL_DIR

# run build
cd $CL_DIR/build/scripts
# ./aws_build_dcp_from_cl.sh  -strategy $STRATEGY -frequency $FREQUENCY -foreground
export CL_NAME=$(basename $CL_DIR)

./aws_build_dcp_from_cl.py -c $CL_NAME --frequency $FREQUENCY --aws_clk_gen --clock_recipe_a A1  --clock_recipe_b B0 --clock_recipe_c C0 --mode small_shell \
    ${PLACE:+--place "$PLACE"} ${PHY_OPT:+--phy_opt "$PHY_OPT"} ${ROUTE:+--route "$ROUTE"} $EXTRA_ARGS
