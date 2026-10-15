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
        Called once per published message.

        In the real tracker node, this callback would:
            1. Deserialize the detection
            2. Pass it to the Kalman filter
            3. Publish the fused track on /tracks

        Here we just log it to prove delivery.
        """
        try:
            detection = json.loads(msg.data)
        except json.JSONDecodeError as e:
            self.get_logger().error(f"Malformed detection message: {e}")
            return

        self.received_count += 1
        classification = detection.get("classification", "UNKNOWN")
        confidence = detection.get("confidence", 0.0)
        frame_id = detection.get("frame_id", -1)

        self.get_logger().info(
            f"[{self.received_count}] Received frame {frame_id}: "
            f"{classification} confidence={confidence}"
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
