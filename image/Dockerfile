# Signoff Gym public base image: the pinned OpenROAD flow image (ORFS) with one kit, sky130hd.
#
# Build it on your own machine before you run a task:
#   docker build -t localhost/signoff-gym-base:sky130hd-1 image/
# The tag starts with localhost/ so a missing image fails to build a task instead of being
# pulled from a public registry under the same name.
#
# This repository ships the recipe, never a built image. A built image carries GPL-3.0 tools
# (OpenSTA inside OpenROAD, and KLayout): see THIRD-PARTY.md before you give one to anyone.
#
# Stage 1 is the upstream image, pinned by digest (the same digest as pins.json). It deletes every
# platform folder except sky130hd, the example designs, the tutorials, the flow's own tests, the
# tuner and the docs. Stage 2 copies what is left into an empty image, so the final image is a
# single layer and no deleted file survives in a lower layer.
FROM openroad/orfs@sha256:73d4aed887dc0552b2c8ad0581e10821ad4f9cf290a2436e54fb5c47ca132120 AS upstream
RUN find /OpenROAD-flow-scripts/flow/platforms -mindepth 1 -maxdepth 1 ! -name sky130hd -exec rm -rf {} + \
 && rm -rf /OpenROAD-flow-scripts/flow/designs /OpenROAD-flow-scripts/flow/tutorials \
      /OpenROAD-flow-scripts/flow/test /OpenROAD-flow-scripts/tools/AutoTuner \
      /OpenROAD-flow-scripts/docs

FROM scratch
COPY --from=upstream / /
# The upstream image's own settings, read with `docker image inspect` on 10 October 2026.
ENV PATH=/usr/local/bin/wrapped-cc:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /OpenROAD-flow-scripts
CMD ["/bin/bash"]
