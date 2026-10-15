"""
Stub publisher node — publishes fake drone detections to /detections.

STUB — replace with real implementation in Week 3.
Real implementation will:
    - Subscribe to /camera/image_raw
    - Run YOLOv8 (TensorRT INT8 quantized) inference on each frame
    - Publish real detection bounding boxes + confidence scores

Why a stub first:
    The ROS 2 Docker pipeline (build, launch, pub/sub) needs to be verified
    before the detector is wired in. Getting the pipeline working with fake
    data is faster and doesn't require the camera image source or the TensorRT
    engine to be ready. Week 3 replaces this node; the topic contract stays the
    same so the subscriber and tracker nodes don't change.

Topic: /detections
Message type: std_msgs/String (JSON-encoded detection)
    {
      "confidence": float,
      "classification": "LIKELY_THREAT" | "UNKNOWN" | "BENIGN",
      "bbox": {"x": float, "y": float, "w": float, "h": float},
      "sensor_source": "EO",
      "frame_id": int
    }

Why std_msgs/String instead of a custom message:
    Custom messages require a colcon build step every time the schema changes.
    JSON over String lets us iterate on the detection schema without rebuilding
    the package. In Week 3 when the schema is stable, switching to a typed
    custom message is a straightforward refactor — the subscriber just changes
    how it deserializes.
"""

import json
import random

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class StubPublisher(Node):
    def __init__(self):
        super().__init__("stub_publisher")

        # QoS depth=10: buffer up to 10 messages if subscriber is slow.
        # For stub testing, depth=10 is sufficient.
        self.publisher_ = self.create_publisher(String, "/detections", 10)

        # Publish at 1 Hz — slow enough to read in the logs, fast enough
        # to show the pipeline is alive.
        # Real detector publishes at the camera frame rate (30 Hz typical).
        self.timer = self.create_timer(1.0, self.publish_detection)
        self.frame_id = 0

        self.get_logger().info("StubPublisher ready — publishing fake detections at 1 Hz")

    def publish_detection(self) -> None:
        # Rotate through classifications to exercise the subscriber's handling
        # of all three classification values.
        classifications = ["BENIGN", "UNKNOWN", "LIKELY_THREAT"]
        classification = classifications[self.frame_id % len(classifications)]

        detection = {
            "confidence": round(random.uniform(0.3, 0.95), 3),
            "classification": classification,
            "bbox": {
                "x": round(random.uniform(0.1, 0.8), 3),
                "y": round(random.uniform(0.1, 0.8), 3),
                "w": round(random.uniform(0.05, 0.3), 3),
                "h": round(random.uniform(0.05, 0.3), 3),
            },
            "sensor_source": "EO",
            "frame_id": self.frame_id,
        }

        msg = String()
        msg.data = json.dumps(detection)
        self.publisher_.publish(msg)

        self.get_logger().info(
            f"Published frame {self.frame_id}: {classification} "
            f"confidence={detection['confidence']}"
        )
        self.frame_id += 1


def main(args=None):
    rclpy.init(args=args)
    node = StubPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
