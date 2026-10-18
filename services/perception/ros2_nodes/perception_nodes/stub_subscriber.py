"""
Stub subscriber node — reads /detections and logs received messages.

STUB — replace with real implementation in Week 3.
Real implementation will:
    - Receive real detection messages from the YOLO detector node
    - Forward detections to the Kalman tracker node for multi-sensor fusion
    - This stub node validates that the ROS 2 Docker pipeline delivers messages

Topic: /detections
Message type: std_msgs/String (JSON-encoded detection, see stub_publisher.py)
"""

import json

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class StubSubscriber(Node):
    def __init__(self):
        super().__init__("stub_subscriber")

        # QoS depth must match or exceed the publisher's depth.
        # depth=10 mirrors the publisher — no messages dropped in normal operation.
        self.subscription = self.create_subscription(
            String,
            "/detections",
            self.on_detection,
            10,
        )
        self.received_count = 0
        self.get_logger().info("StubSubscriber ready — waiting for /detections")

    def on_detection(self, msg: String) -> None:
        """
        Called once per published frame (from yolo_detector or stub_publisher).

        Message format (from yolo_detector):
            {"frame_id": int, "timestamp": str, "detections": [
                {"confidence": float, "classification": str,
                 "bbox": {x, y, w, h}, "sensor_source": "EO"}
            ]}

        In the real tracker node, this callback would pass the detections list
        to the Kalman filter and publish fused tracks on /tracks.
        """
        try:
            frame = json.loads(msg.data)
        except json.JSONDecodeError as e:
            self.get_logger().error(f"Malformed detection message: {e}")
            return

        self.received_count += 1
        frame_id = frame.get("frame_id", -1)
        detections = frame.get("detections", [])

        for det in detections:
            self.get_logger().info(
                f"[{self.received_count}] frame {frame_id}: "
                f"{det.get('classification', 'UNKNOWN')} "
                f"confidence={det.get('confidence', 0.0)}"
            )


def main(args=None):
    rclpy.init(args=args)
    node = StubSubscriber()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
