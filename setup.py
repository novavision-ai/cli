from setuptools import setup, find_packages

setup(
    name='novavision-cli',
    version='0.1.10',
    packages=find_packages(),
    install_requires=["requests",
                      "GPUtil",
                      "psutil",
                      "docker>=6.1.3,<7",
                      "rich",
                      "pyyaml"],
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