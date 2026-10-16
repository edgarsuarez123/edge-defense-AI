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

# No pip packages needed for stub nodes — rclpy and std_msgs come from apt above.
# STUB: When redis_bridge_node is added (Week 3), create ros2_nodes/requirements.txt
# containing redis>=5.0.0 and add:
#   COPY services/perception/ros2_nodes/requirements.txt /tmp/ros2_requirements.txt
#   RUN pip3 install --no-cache-dir -r /tmp/ros2_requirements.txt
# Keep services/perception/requirements.txt separate — it's for host-side training scripts.

# Copy the ROS 2 package source
COPY services/perception/ros2_nodes /ros2_ws/src/perception_nodes

# Build the ROS 2 package
# colcon build compiles the package and sets up install/ with the executables
WORKDIR /ros2_ws
RUN /bin/bash -c "source /opt/ros/humble/setup.bash && colcon build --packages-select perception_nodes"

# Source both ROS 2 and the workspace on every shell invocation
# The ENTRYPOINT below does this for CMD commands
SHELL ["/bin/bash", "-c"]
RUN echo "source /opt/ros/humble/setup.bash" >> /root/.bashrc && \
    echo "source /ros2_ws/install/setup.bash" >> /root/.bashrc

ENTRYPOINT ["/bin/bash", "-c", \
    "source /opt/ros/humble/setup.bash && source /ros2_ws/install/setup.bash && exec \"$@\"", \
    "--"]

CMD ["ros2", "run", "perception_nodes", "stub_publisher"]
