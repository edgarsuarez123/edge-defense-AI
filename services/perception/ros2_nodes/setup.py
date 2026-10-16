from setuptools import setup

package_name = "perception_nodes"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="edge-defense",
    maintainer_email="dev@edge-defense.local",
    description="Drone detector perception nodes",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            # Week 2 stubs — replaced by real detector nodes in Week 3
            "stub_publisher=perception_nodes.stub_publisher:main",
            "stub_subscriber=perception_nodes.stub_subscriber:main",
        ],
    },
)
