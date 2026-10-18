# perception.Dockerfile — ROS 2 Humble + Python perception stack
#
# Base: official ros:humble image (Ubuntu 22.04 + ROS 2 Humble installed via apt).
# ros:humble is the supported LTS release used by defense robotics contractors.
#
# Why not build from ubuntu:22.04 directly:
#   The ros:humble image handles ROS 2 apt repo setup, key management, and
#   ROS_DISTRO / ROS_ROOT env vars. Reproducing that manually is brittle.
#
# What this image contains:
#   - ROS 2 Humble desktop-minimal (rclpy, common message types, no GUI)
#   - Python 3.10 (ships with Ubuntu 22.04)
#   - The perception_nodes ROS 2 package

FROM ros:humble

# Install Python pip and system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-colcon-common-extensions \
    ros-humble-rclpy \
    ros-humble-std-msgs \
    ros-humble-sensor-msgs \
    && rm -rf /var/lib/apt/lists/*

# Copy and build the ROS 2 package BEFORE pip installs.
# pip install torch upgrades setuptools, which breaks colcon's build of
# ament_python packages (canonicalize_version() API change). colcon only
# needs the system setuptools from apt — running it first avoids the conflict.
COPY services/perception/ros2_nodes /ros2_ws/src/perception_nodes
WORKDIR /ros2_ws
RUN /bin/bash -c "source /opt/ros/humble/setup.bash && colcon build --packages-select perception_nodes"

# Install torch with CUDA 12.4 support in a dedicated layer.
# Why separate from ultralytics: torch is ~2GB. Docker layer caching means
# an ultralytics version bump only re-downloads the ~200MB second layer,
# not torch again. If they were one RUN, every ultralytics change re-fetches torch.
#
# Why --extra-index-url instead of plain pip install torch:
# PyPI's default torch wheel is CPU-only. The CUDA wheel lives on PyTorch's
# own index. --extra-index-url adds it as a fallback source; pip picks the
# CUDA wheel because it matches the system's CUDA version.
RUN pip3 install --no-cache-dir \
    torch torchvision \
    --extra-index-url https://download.pytorch.org/whl/cu124

# ultralytics brings in the YOLO API. opencv-python-headless avoids pulling
# Qt/GTK GUI libraries that fail in a headless container.
# numpy is pinned transitively by ultralytics; no explicit pin needed.
RUN pip3 install --no-cache-dir \
    ultralytics==8.4.178 \
    opencv-python-headless

# Tell the NVIDIA Container Toolkit which GPUs and capabilities to expose.
# NVIDIA_VISIBLE_DEVICES=all: the toolkit mounts all available GPUs.
# compute: needed for CUDA programs (torch, TensorRT).
# utility: needed for nvidia-smi and CUDA management APIs.
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Source both ROS 2 and the workspace on every shell invocation
# The ENTRYPOINT below does this for CMD commands
SHELL ["/bin/bash", "-c"]
RUN echo "source /opt/ros/humble/setup.bash" >> /root/.bashrc && \
    echo "source /ros2_ws/install/setup.bash" >> /root/.bashrc

ENTRYPOINT ["/bin/bash", "-c", \
    "source /opt/ros/humble/setup.bash && source /ros2_ws/install/setup.bash && exec \"$@\"", \
    "--"]

CMD ["ros2", "run", "perception_nodes", "stub_publisher"]
