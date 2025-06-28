from setuptools import setup, find_packages

long_description = ""
with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name='novavision-cli',
    version='0.1.32',
    packages=find_packages(),
    include_package_data=True,
    install_requires=["requests==2.32.3",
                      "psutil==6.1.1",
                      "docker>=6.1.3,<7",
                      "rich==13.9.4",
                      "pyyaml==6.0.2"],
    extras_require={
        ":sys_platform=='darwin'": ["pyobjc"]},
    entry_points={
        'console_scripts': ['novavision=novavision.cli:main'],
    },
    author="Kaan",
    author_email="kaanyzc2002@gmail.com",
    description="NovaVision CLI for handling device and servers.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    license="Apache-2.0",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent"
    ],
    python_requires='>=3.8',
)