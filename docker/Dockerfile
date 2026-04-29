# =============================================================================
#  Dockerfile — firesim-lab simulation image
#
#  Build stages
#  ────────────
#  1. base          OS packages, Java, SBT, Verilator, ccache, gosu
#  2. firesim       clone firesim at a pinned commit + build-setup
#  3. firesim-lab   clone firesim-lab + pip install Python deps (via venv)
#  4. final         sbt assembly → seed Ivy/Coursier caches; add entrypoint
#
#  Generic image design
#  ────────────────────
#  The image is built once and pushed to Docker Hub.  It is not tied to any
#  specific host UID/GID.  At container start, entrypoint.sh (running as root)
#  performs lightweight UID/GID remapping:
#    • Appends a transient /etc/passwd + /etc/group entry for the host user.
#    • Adds the host user to the firesim-lab-cache group (GID CACHE_GID).
#    • Sets umask 002 and drops privileges via gosu.
#
#  Cache directories (/home/firesim-lab/.ivy2, .cache/*, .sbt/*) and the
#  Python venv (/opt/firesim-venv) are owned by firesim-lab:firesim-lab-cache
#  with permissions 2775 (setgid + group-writable).  The setgid bit ensures
#  that every file or directory created inside at runtime inherits GID CACHE_GID,
#  so host users remain able to write without any per-start chown.
#
#  HOME is fixed to /home/firesim-lab (set in docker-compose environment) so
#  that SBT, ccache, and pip resolve ~ to the pre-warmed named-volume paths
#  regardless of which UID is running.
#
#  Named-volume seeding
#  ────────────────────
#  Stage 4 populates these paths inside the image layer:
#    /home/firesim-lab/.ivy2/            ← sbt assembly
#    /home/firesim-lab/.cache/coursier/  ← sbt assembly
#    /home/firesim-lab/.sbt/boot/        ← auto-populated by first sbt run
#    /home/firesim-lab/.sbt/1.0/         ← auto-populated by first sbt run
#    /home/firesim-lab/.cache/ccache/    ← verilator compilation cache
#
#  On first container start with empty named volumes, Docker copies these
#  paths from the image layer into the volumes.  Subsequent restarts use
#  the volumes as-is (they grow incrementally with each new target compiled).
#
#  Build arguments
#  ───────────────
#  FIRESIM_COMMIT     git ref for firesim          (default: main)
#  FIRESIM_LAB_COMMIT git ref for firesim-lab       (default: main)
#  SBT_VERSION        SBT launcher version          (default: 1.10.1)
#  JAVA_VERSION       OpenJDK major version         (default: 17)
#  CACHE_GID          GID for firesim-lab-cache     (default: 2543)
#                     Override only if 2543 collides with a host group and
#                     you are rebuilding the image locally — the entrypoint
#                     reads CACHE_GID from the environment at runtime.
#
#  Example build commands
#  ──────────────────────
#  # Standard build (pushed to Docker Hub):
#  docker build -t pentarisc/firesim-lab:latest .
#
#  # Pin a specific firesim commit:
#  docker build --build-arg FIRESIM_COMMIT=v1.19.0 -t firesim-lab:v1.19.0 .
# =============================================================================

# =============================================================================
#  Stage 1 — base
#  OS packages, Java, SBT launcher, Verilator, ccache, gosu
# =============================================================================
FROM ubuntu:24.04 AS base

ARG SBT_VERSION=1.10.1
ARG JAVA_VERSION=17
ARG CACHE_GID=2543

ENV DEBIAN_FRONTEND=noninteractive

# ── System user setup ─────────────────────────────────────────────────────────
# firesim-lab (UID/GID 1000) — fixed build-time identity used to pre-warm
#   caches during `sbt assembly` in Stage 4.  Not used at runtime.
# firesim-lab-cache (GID CACHE_GID, default 2543) — shared group that owns
#   all cache directories with group-write permission.  The entrypoint adds
#   the actual host user to this group on every container start.
#
# Ubuntu 24.04 ships a default 'ubuntu' user at UID 1000; rename it rather
# than trying to create a conflicting entry.
RUN if getent passwd 1000 > /dev/null 2>&1; then \
        existing_user=$(getent passwd 1000 | cut -d: -f1); \
        usermod  -l firesim-lab             "${existing_user}" && \
        usermod  -d /home/firesim-lab -m    firesim-lab && \
        groupmod -n firesim-lab             "${existing_user}"; \
    else \
        groupadd -g 1000 firesim-lab && \
        useradd  -m -u 1000 -g 1000 \
                 -d /home/firesim-lab \
                 -s /bin/bash firesim-lab; \
    fi \
 && groupadd -g "${CACHE_GID}" firesim-lab-cache \
 && usermod -aG firesim-lab-cache firesim-lab

# ── System packages ───────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Privilege-dropping helper used by the entrypoint
    gosu \
    # Version control & download
    git curl wget ca-certificates gnupg \
    # Java runtime (needed by SBT and Scala)
    openjdk-${JAVA_VERSION}-jdk-headless \
    # Python (needed by firesim build scripts and init-target)
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
    libgmp-dev libmpfr-dev libmpc-dev zlib1g-dev \
    libelf-dev libdwarf-dev libusb-1.0-0-dev libffi-dev \
    libexpat1-dev libncurses-dev \
    # Compilers & tools
    gcc g++ binutils make cmake autoconf bison flex \
    # System utilities
    rsync bc universal-ctags unzip patch jq screen parallel \
    graphviz expect mosh \
    # File + shell utils
    file wget sed diffutils findutils \
    gzip bzip2 lzop tar perl bash-completion \
    # QEMU
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

# ── Cache directory setup ─────────────────────────────────────────────────────
# All cache paths are under /home/firesim-lab — a fixed location independent
# of the runtime user's UID.  docker-compose sets HOME=/home/firesim-lab so
# SBT, ccache, and pip all resolve ~ to this path.
#
# Ownership  : firesim-lab:firesim-lab-cache
# Permissions: 2775  (setgid + group-writable)
#   • Owner  (firesim-lab, UID 1000) — full access during image build
#   • Group  (firesim-lab-cache, GID CACHE_GID) — full access at runtime
#   • Setgid — new files/dirs inherit GID CACHE_GID automatically; no chown
#               is ever needed at container start
RUN mkdir -p \
        /home/firesim-lab/.ivy2 \
        /home/firesim-lab/.cache/coursier \
        /home/firesim-lab/.cache/ccache \
        /home/firesim-lab/.sbt/boot \
        /home/firesim-lab/.sbt/1.0 \
 && chown -R firesim-lab:firesim-lab-cache /home/firesim-lab \
 && find /home/firesim-lab -type d -exec chmod 2775 {} \;

# ── Global umask for interactive shells ───────────────────────────────────────
# Ensures bash login/interactive shells also create group-writable files.
# The entrypoint sets umask 002 programmatically for non-interactive use.
RUN echo 'umask 002' > /etc/profile.d/firesim-lab-umask.sh

# ── Environment ───────────────────────────────────────────────────────────────
ENV CCACHE_DIR=/home/firesim-lab/.cache/ccache
ENV CCACHE_MAXSIZE=10G
ENV PATH="/usr/lib/ccache:${PATH}"
# HOME is fixed to the cache home; overriding it in docker-compose environment
# keeps the value consistent even after gosu drops to the host UID.
ENV HOME=/home/firesim-lab
# Expose CACHE_GID so the entrypoint can reference it without hard-coding.
ENV CACHE_GID=${CACHE_GID}

# =============================================================================
#  Stage 2 — firesim
#  Clone firesim at a pinned ref and run build-setup
# =============================================================================
FROM base AS firesim-stage

ARG FIRESIM_COMMIT=main

# /opt/firesim is root-owned, world-readable (755).  No chown required.
RUN git clone https://github.com/firesim/firesim.git /opt/firesim \
    && cd /opt/firesim \
    && git checkout ${FIRESIM_COMMIT} \
    && git config submodule.platforms/f1/aws-fpga.update none \
    && git submodule update --init --recursive

# Stub env.sh — skips build-setup.sh (aws-fpga hdk/sdk handled separately).
RUN echo 'export FIRESIM_ENV_SOURCED=1' >> /opt/firesim/env.sh && \
    echo 'export FS_DIR=/opt/firesim' >> /opt/firesim/env.sh

ENV FIRESIM_ROOT=/opt/firesim
RUN bash -c "cd ${FIRESIM_ROOT}/platforms/f2/aws-fpga-firesim-f2 && \
    source ./sdk_setup.sh && \
    (source ./hdk_setup.sh || echo 'Skipping hdk_setup.sh')"

# =============================================================================
#  Stage 3 — firesim-lab
#  Clone firesim-lab, create venv, install Python dependencies
# =============================================================================
FROM firesim-stage AS firesim-lab-stage

ARG FIRESIM_LAB_COMMIT=main

# /opt/firesim-lab is root-owned, world-readable (755).
RUN git clone https://github.com/pentarisc/firesim-lab.git /opt/firesim-lab \
    && cd /opt/firesim-lab \
    && git checkout ${FIRESIM_LAB_COMMIT}

# ── Python venv ───────────────────────────────────────────────────────────────
# The venv is group-owned by firesim-lab-cache (2775 + setgid) so that:
#   • Python can create __pycache__ dirs at runtime (dirs are group-writable).
#   • Host users in firesim-lab-cache can `pip install` extra packages.
#   • New files/dirs inside the venv automatically inherit GID CACHE_GID.
ENV PATH="/opt/firesim-venv/bin:${PATH}"

RUN python3 -m venv /opt/firesim-venv \
 && chown -R firesim-lab:firesim-lab-cache /opt/firesim-venv \
 && find /opt/firesim-venv -type d -exec chmod 2775 {} \;

# Fix: use the venv's own pip, not the system pip3.
# Using system pip3 would install packages into the system site-packages,
# bypassing the venv entirely.
RUN /opt/firesim-venv/bin/pip install --no-cache-dir --upgrade \
        pip setuptools wheel \
 && /opt/firesim-venv/bin/pip install --no-cache-dir --upgrade pyslang \
 && /opt/firesim-venv/bin/pip install --no-cache-dir \
        -r /opt/firesim-lab/docker/firesim-requirements.txt \
 && /opt/firesim-venv/bin/pip install --no-cache-dir \
        -r /opt/firesim-lab/fslab-cli/requirements.txt

RUN cd /opt/firesim-lab/fslab-cli \
 && /opt/firesim-venv/bin/pip install --no-cache-dir -e .

# Re-apply group ownership and setgid after pip has written new files/dirs.
# Executables in bin/ are 775 so group members can invoke them directly.
RUN chown -R firesim-lab:firesim-lab-cache /opt/firesim-venv \
 && find /opt/firesim-venv -type d -exec chmod 2775 {} \; \
 && find /opt/firesim-venv/bin -type f -exec chmod 775 {} \;

ENV FIRESIM_LAB_ROOT=/opt/firesim-lab

# ── Source tree ownership ─────────────────────────────────────────────────────
# chown the entire /opt/firesim and /opt/firesim-lab trees to
# firesim-lab:firesim-lab-cache so that `sbt assembly` in Stage 4 (running as
# firesim-lab) can create target/ directories wherever it needs to — including
# subproject subdirectories that don't exist yet.
#
# This is a build-time operation that runs once.  At runtime:
#   • Files remain readable by any UID (world-readable permissions from git)
#   • Only firesim-lab (build user) has write access to the source tree,
#     which is correct — users write to /target, not /opt/firesim*
#
# Narrowing this to only /opt/firesim-lab/target and /opt/firesim-lab/project/
# target is NOT sufficient: sbt creates target/ directories inside each
# subproject directory, and it cannot do so if the parent is root-owned 755.
RUN chown -R firesim-lab:firesim-lab-cache /opt/firesim-lab /opt/firesim \
 && find /opt/firesim-lab /opt/firesim -type d -exec chmod g+s {} +

# =============================================================================
#  Stage 4 — final
#  Run sbt assembly as firesim-lab to seed Ivy/Coursier caches in the image
#  layer.  umask 002 ensures every generated file is group-writable from the
#  moment of creation — no post-build chown pass is needed.
# =============================================================================
FROM firesim-lab-stage AS final

USER firesim-lab
# -Dsbt.server.type=none disables SBT's Unix domain socket server.
# The server is only needed for IDE integration (Metals/Bloop) and causes
# permission conflicts in containers: the build-time run creates socket files
# owned by firesim-lab (UID 1000) that runtime users (other UIDs) cannot
# rebind, producing "ServerAlreadyBooting: Permission denied" errors.
ENV SBT_OPTS="-Xmx6g -Xss8m -Dsbt.supershell=false -Dsbt.server.type=none"

RUN umask 002 && cd /opt/firesim-lab && sbt assembly

# ── Clean up SBT server state left by the build-time run ─────────────────────
# Even with sbt.server.type=none, SBT may write active.json and socket files
# under project/target/.sbt/. Remove them so runtime users (any UID) always
# start with a clean slate and never hit stale-socket permission errors.
RUN find /opt/firesim-lab -path "*/project/target/.sbt*" -delete 2>/dev/null || true \
 && find /opt/firesim-lab -name "active.json" -delete 2>/dev/null || true \
 && find /opt/firesim-lab -name "*.sock" -delete 2>/dev/null || true \
 && rm -rf /tmp/.sbt

# ── Fix target directory permissions after sbt assembly ───────────────────────
# The setgid on parent dirs (Stage 3) ensures new subdirectories created by
# sbt inherit the firesim-lab-cache group.  However, tools like zinc explicitly
# write files with mode 600 (rw-------), bypassing umask entirely.  A
# post-build pass corrects the group and restores group-readability and
# writability on all generated target content so that runtime users (any UID
# in firesim-lab-cache) can read and reuse the incremental compile caches.
RUN find /opt/firesim-lab /opt/firesim -path "*/target*" \
        -exec chgrp firesim-lab-cache {} + \
 && find /opt/firesim-lab /opt/firesim -path "*/target*" -type f \
        -exec chmod 664 {} + \
 && find /opt/firesim-lab /opt/firesim -path "*/target*" -type d \
        -exec chmod 2775 {} +

# ── Cache statistics (informational) ─────────────────────────────────────────
RUN echo "" && \
    echo "=== Ivy2 cache size ===" && \
    du -sh /home/firesim-lab/.ivy2 2>/dev/null || true && \
    echo "=== Coursier cache size ===" && \
    du -sh /home/firesim-lab/.cache/coursier 2>/dev/null || true && \
    echo "=== ccache statistics ===" && \
    ccache --show-stats && \
    echo ""

# ── Entrypoint installation ───────────────────────────────────────────────────
# Switch back to root to install the entrypoint script.
# The entrypoint runs as root, performs UID remapping, then drops privileges.
USER root

COPY entrypoint.sh       /usr/local/bin/entrypoint.sh
COPY firesim-lab-shell   /usr/local/bin/firesim-lab-shell
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/firesim-lab-shell

# ── Runtime environment ───────────────────────────────────────────────────────
ENV FIRESIM_ROOT=/opt/firesim
ENV FIRESIM_LAB_ROOT=/opt/firesim-lab
ENV CCACHE_DIR=/home/firesim-lab/.cache/ccache
ENV SBT_OPTS="-Xmx8g -Xss8m -Dsbt.supershell=false -Dsbt.server.type=none"

# Clean the ccache which might have been created as root.
RUN rm -rf /home/firesim-lab/.cache/ccache/*

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/bin/bash"]