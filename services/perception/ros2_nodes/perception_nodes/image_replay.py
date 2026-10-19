"""
Image replay node — reads images from a directory, publishes as sensor_msgs/Image.

What it does:
    Simulates a camera feed by reading .jpg/.png images from a directory and
    publishing each as sensor_msgs/Image on /camera/image_raw at a configurable
    FPS. This is the image source for yolo_detector during dev and benchmarking.

Why it exists:
    AirSim/Gazebo are wired up in Week 5-6. Until then, this node drives the
    perception pipeline with real test set images so we can measure FPS and mAP
    against known ground truth.

Why manual Image conversion and not cv_bridge:
    cv_bridge.CvBridge().cv2_to_imgmsg() requires ros-humble-cv-bridge — a
    compiled C++ ROS package that adds a non-trivial apt dependency and build
    step. The manual conversion is 4 lines (set height, width, step, encoding,
    data) and is completely transparent. When Gazebo takes over as the image
    source, this node is retired and cv_bridge is irrelevant.

Usage:
    ros2 run perception_nodes image_replay --ros-args \\
        -p image_dir:=/data/test/images \\
        -p fps:=60.0 \\
        -p loop:=true
"""

import glob
import os

import cv2
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile
from sensor_msgs.msg import Image


class ImageReplay(Node):
    def __init__(self):
        super().__init__("image_replay")

        self.declare_parameter("image_dir", "")
        self.declare_parameter("fps", 60.0)
        self.declare_parameter("loop", True)
        # How many images to pre-load into RAM. Pre-loading removes the WSL2
        # filesystem cross-VM overhead (~300ms/read) so the timer isn't disk-bound.
        # 500 images decoded as BGR arrays = ~600MB RAM — safe for WSL2.
        # Loading all 30k images would be ~36GB (640x640x3 bytes each) and OOMs.
        # 500 frames looping at 60 FPS gives a stable Hz measurement in <10s.
        self.declare_parameter("preload_count", 500)

        image_dir = self.get_parameter("image_dir").value
        fps = float(self.get_parameter("fps").value)
        self.loop = self.get_parameter("loop").value
        preload_count = int(self.get_parameter("preload_count").value)

        if not image_dir:
            self.get_logger().error("image_dir parameter is required")
            raise RuntimeError("image_dir not set")

        # Glob both extensions, sort so replay order is deterministic across runs.
        paths = sorted(
            glob.glob(os.path.join(image_dir, "*.jpg"))
            + glob.glob(os.path.join(image_dir, "*.png"))
        )
        if not paths:
            self.get_logger().error(f"No images found in {image_dir}")
            raise RuntimeError(f"No images in {image_dir}")

        # Pre-load a batch of images into RAM so _publish_frame has zero disk I/O.
        # WSL2 Windows filesystem mounts add ~300ms per cv2.imread call, which
        # caps throughput at ~3 FPS regardless of the timer rate. Loading a fixed
        # batch at startup removes the bottleneck — callbacks then run in <1ms.
        # We cap at preload_count (default 500) to stay within WSL2 RAM limits:
        # 500 images decoded as BGR arrays ~= 600MB; loading all 30k would be ~36GB.
        batch = paths[:preload_count]
        self.get_logger().info(f"Pre-loading {len(batch)}/{len(paths)} images into RAM...")
        self.frames = []
        self.paths = []
        for p in batch:
            img = cv2.imread(p)
            if img is not None:
                self.frames.append(img)
                self.paths.append(p)
        self.get_logger().info(f"Loaded {len(self.frames)} images from {image_dir}")

        # depth=1: if the detector is slow, it only ever sees the latest frame.
        # The detector's own depth=1 subscriber enforces this at its end too.
        self.pub = self.create_publisher(Image, "/camera/image_raw", QoSProfile(depth=1))
        self.timer = self.create_timer(1.0 / fps, self._publish_frame)
        self.index = 0

    def _publish_frame(self) -> None:
        if self.index >= len(self.frames):
            if self.loop:
                self.index = 0
            else:
                self.get_logger().info("Replay complete — all images published")
                self.timer.cancel()
                return

        img = self.frames[self.index]

        # Manual conversion: sensor_msgs/Image has no opaque format —
        # setting these 5 fields is all cv_bridge does internally.
        msg = Image()
        msg.height = img.shape[0]
        msg.width = img.shape[1]
        msg.encoding = "bgr8"         # matches cv2.imread output channel order
        msg.step = img.shape[1] * 3  # bytes per row = width * channels
        msg.data = img.tobytes()

        self.pub.publish(msg)
        self.index += 1

        # Log on first frame and every 100th to avoid spamming at 60 FPS.
        if self.index == 1 or self.index % 100 == 0:
            self.get_logger().info(
                f"Frame {self.index}/{len(self.frames)}: {os.path.basename(self.paths[self.index - 1])}"
            )


def main(args=None):
    rclpy.init(args=args)
    node = ImageReplay()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
