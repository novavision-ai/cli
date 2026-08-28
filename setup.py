from setuptools import setup, find_packages
from novavision import __version__

long_description = ""
with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="novavision-cli",
    version=__version__,
    packages=find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    install_requires=[
        "requests==2.32.3",
        "psutil==6.1.1",
        "docker>=6.1.3,<7",
        "rich==13.9.4",
        "pyyaml==6.0.2",
    ],
    extras_require={
        ":sys_platform=='darwin'": ["pyobjc"],
        "test": [
            "pytest>=7.4",
            "pytest-cov>=4.1",
            "ruff>=0.6",
        ],
    },
    entry_points={
        "console_scripts": ["novavision=novavision.cli:main"],
    },
    author="İlhan Kaan Yazıcıoğlu, Selçuk Öz",
    author_email="ilhan.kaan.yazicioglu@diginova.com.tr, selcuk.oz@diginova.com.tr",
    description="NovaVision CLI for handling servers.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    license="Apache-2.0",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
)
