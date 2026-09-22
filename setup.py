from setuptools import setup, find_packages

setup(
    name="tsm-mesh",
    version="1.0.0",
    description="Tactical SDR Mesh Network (TSM-Net SG) over Continuous I/Q Baseband",
    author="TSM-Net SG Engineering",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "numpy>=1.17.0",
        "PyYAML>=5.3.0",
    ],
    entry_points={
        "console_scripts": [
            "tsm-node=tsm.network.orchestrator:main",
            "tsm-modem=tsm.modem.sdr_driver:main",
            "tsm-chat=tsm.apps.chat:main",
        ],
    },
)
