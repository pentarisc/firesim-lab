# =============================================================================
#  Dockerfile — firesim-lab simulation image
#
#  Build stages
#  ────────────
#  1. base          OS packages, Java, SBT, Verilator, ccache
#  2. firesim       clone firesim at a pinned commit + build-setup
#  3. firesim-lab   clone firesim-lab + pip install Python deps
#  4. cache-warm    sbt update/compile → populate Ivy/Coursier caches
#  5. final         build examples → populate Verilator ccache
#
#  Named-volume seeding
#  ────────────────────
#  Stages 4 and 5 populate paths inside the image layer:
#    ~/.ivy2/                  ← sbt update
#    ~/.cache/coursier/        ← sbt update
#    ~/.sbt/boot/              ← auto-populated by first sbt invocation
#    ~/.sbt/1.0/               ← auto-populated by first sbt invocation
#    ~/.cache/ccache/          ← make verilator on both examples
#
#  When docker-compose starts a container with empty named volumes, Docker
#  copies these paths from the image layer into the volumes on first start.
#  Subsequent container restarts use the volumes as-is (they grow
#  incrementally with each new target elaborated by the user).
#
#  Build arguments
#  ───────────────
#  FIRESIM_COMMIT     git ref for firesim         (default: main)
#  FIRESIM_LAB_COMMIT git ref for firesim-lab      (default: main)
#  SBT_VERSION        version to install            (default: 1.9.7)
#  JAVA_VERSION       OpenJDK version               (default: 11)
#
#  Example build commands
#  ──────────────────────
#  # Standard build:
#  docker build -t firesim-lab:latest .
#
#  # Pin a specific firesim commit:
#  docker build --build-arg FIRESIM_COMMIT=v1.19.0 -t firesim-lab:v1.19.0 .
#
#  # Skip example compilation (faster build, cold ccache):
#  docker build --target cache-warm -t firesim-lab:no-examples .
# =============================================================================

# =============================================================================
#  Stage 1 — base
#  OS packages, Java, SBT launcher, Verilator, ccache
# =============================================================================
FROM ubuntu:22.04 AS base

ARG SBT_VERSION=1.10.1
ARG JAVA_VERSION=17
ARG HOST_UID=1000
ARG HOST_GID=1000

RUN groupadd -g ${HOST_GID} firesim-lab && \
    useradd -m -u ${HOST_UID} -g ${HOST_GID} -s /bin/bash firesim-lab

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    # Version control & download
    git curl wget ca-certificates gnupg \
    # Java runtime (needed by SBT and Scala)
    openjdk-${JAVA_VERSION}-jdk-headless \
    # Python (needed by firesim build scripts and new-target.py)
    python3 python3-pip python3-venv python3-dev \
    # Verilator simulator
    verilator \
    # ccache — caches g++ compilation of Verilator-generated C++
    ccache \
    # Build tools
    make g++ build-essential gawk \
    # Useful utilities
    jq tree device-tree-compiler libguestfs-tools libgmp-dev \
    && rm -rf /var/lib/apt/lists/*

# --- FireSim environment (from firesim.yaml) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core build + toolchain libs
    gmp-dev libmpfr-dev libmpc-dev zlib1g-dev \
    libelf-dev libdwarf-dev libusb-1.0-0-dev libffi-dev \
    libexpat1-dev libncurses-dev \
    # Compilers & tools
    gcc g++ binutils make cmake autoconf bison flex \
    # System utilities
    rsync bc ctags unzip patch jq screen parallel \
    graphviz expect mosh \
    # File + shell utils
    file wget which sed diffutils findutils \
    gzip bzip2 lzop tar perl bash-completion \
    # QEMU (approximation)
    qemu-system-x86 qemu-utils \
    # Device tree compiler
    device-tree-compiler \
    # Debug / ELF tools
    elfutils \
    # Editors
    vim \
    && rm -rf /var/lib/apt/lists/*

# ── SBT ───────────────────────────────────────────────────────────────────────
RUN curl -fsSL \
    "https://github.com/sbt/sbt/releases/download/v${SBT_VERSION}/sbt-${SBT_VERSION}.tgz" \
    -o /tmp/sbt.tgz \
 && mkdir -p /opt/sbt \
 && tar -xzf /tmp/sbt.tgz -C /opt/sbt --strip-components=1 \
 && ln -s /opt/sbt/bin/sbt /usr/local/bin/sbt \
 && rm /tmp/sbt.tgz

# ── ccache ────────────────────────────────────────────────────────────────────
# Store the cache at the path that will be bind-mounted as a named volume.
# MAX_SIZE controls how much disk the cache may consume; adjust for your host.
ENV CCACHE_DIR=/home/firesim-lab/.cache/ccache
ENV CCACHE_MAXSIZE=10G
ENV PATH="/usr/lib/ccache:${PATH}"
ENV HOME=/home/firesim-lab

# =============================================================================
#  Stage 2 — firesim
#  Clone firesim at a pinned ref and run build-setup
# =============================================================================
FROM base AS firesim-stage

ARG FIRESIM_COMMIT=main

RUN git clone https://github.com/firesim/firesim.git /firesim \
    && cd /firesim \
    && git checkout ${FIRESIM_COMMIT}

# build-setup.sh initialises submodules, installs Python deps, and
# runs the RISC-V toolchain setup (--skip-validate skips the slow toolchain check)
RUN cd /firesim && ./build-setup.sh --skip-validate

ENV FIRESIM_ROOT=/firesim

# =============================================================================
#  Stage 3 — firesim-lab
#  Clone firesim-lab at a pinned ref and install Python dependencies
# =============================================================================
FROM firesim-stage AS firesim-lab-stage

ARG FIRESIM_LAB_COMMIT=main

RUN git clone https://github.com/pentarisc/firesim-lab.git /firesim-lab
    && cd /firesim-lab \
    && git checkout ${FIRESIM_LAB_COMMIT}

# Instsall firesim requirements (as per /firesim/conda-reqs/firesim.yaml)
# as well as new-target.py dependencies (click, jinja2, pyyaml)
ENV PATH="/opt/firesim-venv/bin:${PATH}"
RUN python3 -m venv /opt/firesim-venv

RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /firesim-lab/docker/firesim-requirements.txt &&
RUN pip3 install --no-cache-dir \
    -r /firesim-lab/scripts/requirements.txt

ENV FIRESIM_LAB_ROOT=/firesim-lab
RUN chown -R firesim-lab:firesim-lab /firesim /firesim-lab

# =============================================================================
#  Stage 4 — cache-warm
#  Run sbt update + compile on firesim and firesim-lab to seed
#  ~/.ivy2/ and ~/.cache/coursier/ inside the image layer.
#
#  `|| true` tolerates build failures caused by the absence of a user
#  target project — we only care that dependency JARs are downloaded.
# =============================================================================
FROM firesim-lab-stage AS cache-warm

# Caches should be created in user's home directory. So run this layer as user.
USER firesim-lab

ENV SBT_OPTS="-Xmx6g -Xss8m -Dsbt.supershell=false"

# ── Resolve + download all firesim dependencies ───────────────────────────────
RUN cd /firesim/sim && \
    sbt ${SBT_OPTS} \
      "project firesim"    update \
      "project midas"      update \
      "project targetutils" update \
    || true

# ── Compile firesim framework projects ───────────────────────────────────────
# Compiling (not just resolving) warms SBT's incremental analysis cache
# under /firesim/sim/target/ so subsequent compilations are much faster.
RUN cd /firesim/sim && \
    sbt ${SBT_OPTS} \
      "project targetutils" compile \
      "project midas"       compile \
    || true

# ── Resolve + compile firesim-lab common bridge library ───────────────────────
RUN cd /firesim-lab && \
    FIRESIM_ROOT=/firesim \
    sbt ${SBT_OPTS} \
      "project common" update \
      "project common" compile \
    || true

# =============================================================================
#  Stage 5 — final  (default build target)
#  Build both example projects with Verilator to warm ~/.cache/ccache/
#
#  This stage:
#    • Runs firesim-lab/examples/build-examples.sh
#    • Calls `make elaborate` + `make verilator` for each example
#    • Each `make verilator` compiles ~200 C++ files through ccache
#    • Subsequent user compilations of different targets hit the ccache
#      for common infrastructure files (harness, bridge drivers, FASED model)
#      and only recompile target-specific generated C++
#
#  To skip this stage (faster image build, cold ccache at runtime):
#    docker build --target cache-warm -t firesim-lab:no-examples .
# =============================================================================
FROM cache-warm AS final

# Run example builds.  Failures print a warning but do not abort the build.
RUN FIRESIM_ROOT=/firesim \
    FIRESIM_LAB_ROOT=/firesim-lab \
    /firesim-lab/examples/build-examples.sh

# ── Print final cache statistics ──────────────────────────────────────────────
RUN echo "" && \
    echo "=== Ivy2 cache size ===" && \
    du -sh /home/firesim-lab/.ivy2 2>/dev/null || true && \
    echo "=== Coursier cache size ===" && \
    du -sh /home/firesim-lab/.cache/coursier 2>/dev/null || true && \
    echo "=== ccache statistics ===" && \
    ccache --show-stats && \
    echo ""

# ── Runtime environment ───────────────────────────────────────────────────────
ENV FIRESIM_ROOT=/firesim
ENV FIRESIM_LAB_ROOT=/firesim-lab
ENV CCACHE_DIR=/home/firesim-lab/.cache/ccache
ENV SBT_OPTS="-Xmx8g -Xss8m -Dsbt.supershell=false"

# Default working directory — docker-compose bind-mounts the user's target
# project here, so `source env.sh && make elaborate` works immediately.
USER firesim-lab

CMD ["/bin/bash"]
