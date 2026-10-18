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

        image_dir = self.get_parameter("image_dir").value
        fps = float(self.get_parameter("fps").value)
        self.loop = self.get_parameter("loop").value

        if not image_dir:
            self.get_logger().error("image_dir parameter is required")
            raise RuntimeError("image_dir not set")

        # Glob both extensions, sort so replay order is deterministic across runs.
        self.images = sorted(
            glob.glob(os.path.join(image_dir, "*.jpg"))
            + glob.glob(os.path.join(image_dir, "*.png"))
        )
        if not self.images:
            self.get_logger().error(f"No images found in {image_dir}")
            raise RuntimeError(f"No images in {image_dir}")

        self.get_logger().info(f"Loaded {len(self.images)} images from {image_dir}")

        # depth=1: if the detector is slow, it only ever sees the latest frame.
        # The detector's own depth=1 subscriber enforces this at its end too.
        self.pub = self.create_publisher(Image, "/camera/image_raw", QoSProfile(depth=1))
        self.timer = self.create_timer(1.0 / fps, self._publish_frame)
        self.index = 0

    def _publish_frame(self) -> None:
        if self.index >= len(self.images):
            if self.loop:
                self.index = 0
            else:
                self.get_logger().info("Replay complete — all images published")
                self.timer.cancel()
                return

        path = self.images[self.index]
        img = cv2.imread(path)
        if img is None:
            self.get_logger().warning(f"Could not read {path}, skipping")
            self.index += 1
            return

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
                f"Frame {self.index}/{len(self.images)}: {os.path.basename(path)}"
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
