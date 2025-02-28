from setuptools import setup, find_packages

setup(
    name='novavision-cli',
    version='0.1.15',
    packages=find_packages(),
    install_requires=["requests==2.32.3",
                      "GPUtil==1.4.0",
                      "psutil==6.1.1",
                      "docker>=6.1.3,<7",
                      "rich==13.9.4",
                      "pyyaml==6.0.2"],
    entry_points={
        'console_scripts': ['novavision=novavision.cli:main'],
    },
    author="Kaan",
    author_email="kaanyzc2002@gmail.com",
    description="CLI example",
    license="Apache-2.0",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent"
    ],
    python_requires='>=3.8',
)