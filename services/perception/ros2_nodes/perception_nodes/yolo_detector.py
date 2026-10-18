"""
YOLO detector node — subscribes to /camera/image_raw, runs YOLOv8 inference,
publishes per-frame JSON detections to /detections.

What it does:
    Converts incoming sensor_msgs/Image messages to numpy arrays, runs the
    Ultralytics YOLO model, filters by confidence threshold, and publishes
    a JSON envelope containing every detection found in that frame.

Why per-frame JSON envelope (not one message per detection):
    The downstream Kalman tracker needs all detections from a single frame
    at once to associate them by timestamp. One message per detection would
    require the tracker to group by frame_id before associating — extra logic
    for no benefit.

Why QoS depth=1 BEST_EFFORT on the camera subscriber:
    Counter-UAS needs the freshest frame available. If inference takes 30ms
    and the camera publishes at 60 FPS, half the frames are dropped — that
    is correct behavior. BEST_EFFORT tells ROS 2 to deliver what it can
    without buffering. Without it, depth=1 alone still allows head-of-line
    blocking under RELIABLE delivery semantics.

Why Ultralytics YOLO() and not raw TensorRT:
    model.predict() accepts .pt, .onnx, and .engine files transparently.
    Switching to TensorRT INT8 = change model_path from best.pt to best.engine.
    Zero inference code changes needed. Writing raw TensorRT now would lock in
    a more complex I/O path before we've validated the model.

Usage:
    ros2 run perception_nodes yolo_detector --ros-args \\
        -p model_path:=/models/best.pt \\
        -p conf_threshold:=0.25 \\
        -p device:=cuda:0
"""

import json
from datetime import datetime, timezone

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String
from ultralytics import YOLO


class YoloDetector(Node):
    def __init__(self):
        super().__init__("yolo_detector")

        self.declare_parameter("model_path", "/models/best.pt")
        self.declare_parameter("conf_threshold", 0.25)
        self.declare_parameter("device", "cuda:0")

        model_path = self.get_parameter("model_path").value
        self.conf_threshold = float(self.get_parameter("conf_threshold").value)
        self.device = self.get_parameter("device").value

        self.get_logger().info(f"Loading model from {model_path} on {self.device}")
        self.model = YOLO(model_path)
        self.model.to(self.device)
        self.get_logger().info("Model loaded — waiting for images")

        # depth=1 + BEST_EFFORT: only keep the latest frame.
        # When inference lags behind the publish rate, stale frames are dropped
        # rather than queued. A stale detection is worse than no detection.
        sub_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.sub = self.create_subscription(Image, "/camera/image_raw", self._on_image, sub_qos)

        # depth=10: downstream subscriber (tracker or stub) should receive every
        # detection frame. We have already dropped stale camera frames upstream —
        # there is no reason to drop detections on this side.
        self.pub = self.create_publisher(String, "/detections", 10)
        self.frame_count = 0

    def _on_image(self, msg: Image) -> None:
        # Reconstruct numpy array from raw bytes.
        # encoding="bgr8" means 3 uint8 channels per pixel in BGR order —
        # exactly what cv2.imread() produces, so the model sees the same
        # channel order it was trained on.
        img = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 3)

        results = self.model.predict(
            img,
            conf=self.conf_threshold,
            device=self.device,
            verbose=False,   # suppresses per-frame Ultralytics console output
        )

        detections = []
        for box in results[0].boxes:
            # xyxyn: normalized [x1, y1, x2, y2] in 0..1 range.
            # Convert to [x, y, w, h] top-left origin format (same as stub_publisher).
            x1, y1, x2, y2 = box.xyxyn[0].tolist()
            detections.append({
                "confidence": round(float(box.conf[0]), 4),
                "classification": "drone",
                "bbox": {
                    "x": round(x1, 4),
                    "y": round(y1, 4),
                    "w": round(x2 - x1, 4),
                    "h": round(y2 - y1, 4),
                },
                "sensor_source": "EO",
            })

        self.frame_count += 1

        out = String()
        out.data = json.dumps({
            "frame_id": self.frame_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "detections": detections,
        })
        self.pub.publish(out)

        # Log every 100th frame, or any frame with 3+ detections (unusual, worth flagging).
        if self.frame_count % 100 == 0 or len(detections) >= 3:
            self.get_logger().info(
                f"Frame {self.frame_count}: {len(detections)} detection(s)"
            )


def main(args=None):
    rclpy.init(args=args)
    node = YoloDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
