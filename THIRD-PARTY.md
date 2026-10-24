# Third-party components and what each one asks of you

Signoff Gym's own code and docs are under the Apache License 2.0 (see `LICENSE`). This file lists everything else: what is in this repository, what is only inside the image you build from `image/Dockerfile`, and what is left out on purpose.

This is a plain-words summary, not legal advice. The licence names come from each project's own files and pages as read in October 2026; where we only read a label, it says so.

## 1. In this repository

| What | Where | Licence | What it asks |
|---|---|---|---|
| SkyWater SKY130 PDK licence text | `tasks/*/LICENSE-sky130.txt` (copied from github.com/google/skywater-pdk, branch main at 7198cf6, on 8 October 2026) | Apache-2.0 | Keep the licence text with the kit's files. Each task's `NOTICE.md` says the task uses the sky130hd kit under it. |
| Test fixtures from tool runs | `tests/fixtures/gcd_stock_1p1/`, `tests/fixtures/riscv32i_stock_6p0/` (metrics the OpenROAD flow wrote for its stock gcd and riscv32i examples) and `tests/fixtures/signoff/` (KLayout DRC and LVS reports) | Tool output: numbers and report text, no design source | Nothing beyond this note. |
| The upstream image's name and digest | `pins.json`, `image/Dockerfile` (`openroad/orfs@sha256:73d4aed8...`, ORFS tag 26Q3-615-gdb8b985f8) | A reference only; nothing of the image is in this repository | See section 2 before you share a built image. |

Everything else in this repository was written for Signoff Gym and is under Apache-2.0. The two task designs (`tasks/*/environment/workspace/rtl/design.v` and the reference in `solution/solve.sh`) were generated for these tasks; they have no public source.

## 2. Only inside the image you build (never shipped by this repository)

`image/Dockerfile` starts from the pinned OpenROAD flow image (ORFS), keeps only the sky130hd kit plus the two files the flow reads from other platform folders (`common/lcu_kogge_stone.v` and `sky130hs/rcx_patterns.rules`), and flattens the result into one layer. The tasks then add a few packages. A built image holds:

| Component | Licence, and where it was read | What sharing a built image asks |
|---|---|---|
| OpenROAD | BSD 3-Clause | Keep its copyright notice and licence text. |
| OpenSTA, the timer built into OpenROAD | GPL v3. Its README: "OpenSTA is dual licensed. It is released under GPL v3 as OpenSTA and is also licensed for commerical applications by Parallax Software without the GPL's requirements." | Give the licence text and the complete corresponding source code with the image, or a written offer for it, and add no extra restrictions. |
| OpenROAD-flow-scripts (the flow scripts) | BSD 3-Clause. Its README: "The OpenROAD-flow-scripts repository (build and run scripts) has a BSD 3-Clause License." | Keep its copyright notice and licence text. |
| Yosys (synthesis) | ISC | Keep the copyright notice and licence text. |
| KLayout (DRC and LVS) | GPL-3.0, as its GitHub repository is labelled (we read the label, not the full licence file) | As for OpenSTA: licence text plus complete corresponding source, or a written offer. |
| kepler-formal, the flow's formal equivalence checker (in the upstream image). Both tasks run it: the flow sets LEC_CHECK to 1 whenever its binary exists (`flow/scripts/variables.yaml`; `flow/scripts/variables.mk` line 132 names the binary) and neither task sets LEC_CHECK, so every flow run checks the netlist after CTS repair and again at the finish stage. In oracle grades of both tasks on 23 October 2026 the grader's logs say "Repair timing output passed lec test" and "Global output passed lec test". The agent's Dockerfile locks the binary to the flow's own user, so the agent cannot start it directly. | GPL-3.0-only: the SPDX line in its `CMakeLists.txt`; its `LICENSE.rst` is the GPL v3 text (read at commit f70c2e3, the one the pinned flow builds, on 23 October 2026) | As for OpenSTA: licence text plus complete corresponding source, or a written offer. |
| SkyWater sky130hd platform files | Apache-2.0 | Keep the licence text and any notices; say what you changed. |
| Two files from other ORFS platform folders: `flow/platforms/common/lcu_kogge_stone.v` (synthesis maps with it) and `flow/platforms/sky130hs/rcx_patterns.rules` (sky130hd's `rcx_patterns.rules` is a link to it; the finish stage's RC extraction reads it) | Not read: in the pinned image neither folder has a licence file and neither file has a licence header (read 23 October 2026) | Read their licences before sharing an image. |
| Ubuntu 22.04 base packages | Each package's own licence, in `/usr/share/doc/<package>/copyright` inside the image | Keep those files in the image. |
| Icarus Verilog `iverilog` 11.0-1.1, added to the agent image by each task's Dockerfile | GPL v2 (Ubuntu's package) | Licence text plus the source, or a written offer. |
| tmux, added to the agent image (Harbor's terminus-2 agent uses it) | Ubuntu's package; its licence is in `/usr/share/doc/tmux/copyright` (we have not read it) | Keep that file. |

The simplest way to meet all of this is the one this repository takes: share the recipe, not a built image. Anyone who builds it pulls the upstream image from its own publisher. If you ever give a built image to someone, put the source code of the GPL programs beside it and check each line above first.

## 3. Left out on purpose

- **The other kits in the upstream image.** The upstream ORFS image carries several more kits. The recipe deletes every platform folder except sky130hd (it keeps one file each from `common` and `sky130hs`, the two the flow reads), and the flattening stage makes sure no deleted file survives in a lower layer. Two of the deleted kits, for the record: ASAP7, a predictive 7 nm kit, is BSD 3-Clause, and its university's site has called it "an academic and research aid only"; GT2N, a predictive 2 nm kit, is BSD 3-Clause. Neither is used here.
- **The ORFS example designs** (`flow/designs`), tutorials, the flow's own tests, the tuner and the docs: deleted by the recipe; the tasks do not use them. If a later public task uses one of those designs, its own notice comes with it. For example, riscv32i's folder carries Apache-2.0 text that names no copyright holder, and the jpeg encoder needs its ASICs World Services attribution and Richard Herveille's notice in every source file.

## 4. Tools you install yourself

These are not in this repository and not in the image: Docker (with its compose and buildx plugins), uv, Harbor 0.23.0 (the task runner, installed by you into `.venv-harbor`), and pytest 9.1.1 (installed by `tools/setup.sh`). Each comes with its own licence.
