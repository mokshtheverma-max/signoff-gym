# Signoff Gym public base image: the pinned OpenROAD flow image (ORFS) with one kit, sky130hd.
#
# Build it on your own machine before you run a task:
#   docker build -t localhost/signoff-gym-base:sky130hd-1 image/
# The tag starts with localhost/ so a missing image fails to build a task instead of being
# pulled from a public registry under the same name.
#
# This repository ships the recipe, never a built image. A built image carries GPL-3.0 tools
# (OpenSTA inside OpenROAD, KLayout and kepler-formal): see THIRD-PARTY.md before you give one to
# anyone.
#
# Stage 1 rebuilds kepler-formal, the flow's formal equivalence checker, which every flow run of
# these tasks uses (LEC_CHECK is 1 whenever its binary exists). It fetches the source the pinned
# image was built from (ORFS pins it as the submodule tools/kepler-formal at f70c2e3) and builds it
# the way that ORFS's build_openroad.sh does, without -march=native: the upstream build used it, so
# the upstream binary and its libraries carry AVX-512 instructions and crash on a CPU without them.
# This stage needs the network (GitHub) once.
# Stage 2 is the upstream image, pinned by digest (the same digest as pins.json). It deletes every
# platform folder except sky130hd, and keeps the only two files the flow reads from other platform
# folders: common/lcu_kogge_stone.v (synthesis maps with it) and sky130hs/rcx_patterns.rules
# (sky130hd's rcx_patterns.rules is a link to it, and the finish stage's RC extraction reads it).
# It also deletes the example designs, the tutorials, the flow's own tests, the tuner, the docs
# and its own kepler-formal, which it takes from stage 1 instead. Stage 3 copies what is left into
# an empty image, so the final image is a single layer and no deleted file survives in a lower
# layer.
FROM openroad/orfs@sha256:73d4aed887dc0552b2c8ad0581e10821ad4f9cf290a2436e54fb5c47ca132120 AS kepler-formal
ADD https://github.com/keplertech/kepler-formal.git#f70c2e38592d704c8cccf68433b091e82c8118f0 /OpenROAD-flow-scripts/tools/kepler-formal
RUN rm -rf /OpenROAD-flow-scripts/tools/install/kepler-formal \
 && cmake -B tools/kepler-formal/build tools/kepler-formal \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CXX_FLAGS_RELEASE="-Ofast -ffast-math -flto" \
      -DCMAKE_EXE_LINKER_FLAGS="-flto" \
      -DCMAKE_BUILD_RPATH="/OpenROAD-flow-scripts/tools/kepler-formal/build/thirdparty/naja/src/dnl:/OpenROAD-flow-scripts/tools/kepler-formal/build/thirdparty/naja/src/nl/nl:/OpenROAD-flow-scripts/tools/kepler-formal/build/thirdparty/naja/src/optimization" \
      -DCMAKE_INSTALL_RPATH="/OpenROAD-flow-scripts/tools/install/kepler-formal/lib" \
      -DCMAKE_BUILD_WITH_INSTALL_RPATH=OFF \
      -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF \
      -DCMAKE_INSTALL_PREFIX="/OpenROAD-flow-scripts/tools/install/kepler-formal" \
 && cmake --build tools/kepler-formal/build --target install -j "$(nproc)"

FROM openroad/orfs@sha256:73d4aed887dc0552b2c8ad0581e10821ad4f9cf290a2436e54fb5c47ca132120 AS upstream
RUN find /OpenROAD-flow-scripts/flow/platforms -mindepth 1 -maxdepth 1 ! -name sky130hd ! -name common ! -name sky130hs -exec rm -rf {} + \
 && find /OpenROAD-flow-scripts/flow/platforms/common -mindepth 1 -maxdepth 1 ! -name lcu_kogge_stone.v -exec rm -rf {} + \
 && find /OpenROAD-flow-scripts/flow/platforms/sky130hs -mindepth 1 -maxdepth 1 ! -name rcx_patterns.rules -exec rm -rf {} + \
 && rm -rf /OpenROAD-flow-scripts/flow/designs /OpenROAD-flow-scripts/flow/tutorials \
      /OpenROAD-flow-scripts/flow/test /OpenROAD-flow-scripts/tools/AutoTuner \
      /OpenROAD-flow-scripts/docs /OpenROAD-flow-scripts/tools/install/kepler-formal
COPY --from=kepler-formal /OpenROAD-flow-scripts/tools/install/kepler-formal /OpenROAD-flow-scripts/tools/install/kepler-formal

FROM scratch
COPY --from=upstream / /
# The upstream image's own settings, read with `docker image inspect` on 10 October 2026.
ENV PATH=/usr/local/bin/wrapped-cc:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /OpenROAD-flow-scripts
CMD ["/bin/bash"]
