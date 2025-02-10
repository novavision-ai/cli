from setuptools import setup, find_packages

setup(
    name='novavision-cli',
    version='0.1',
    packages=find_packages(),
    install_requires=["requests",
                      "GPUtil",
                      "psutil",
                      "docker"],
    entry_points={
        'console_scripts': ['novavision-cli=novavision.cli:main'],
    },
    author="Kaan",
    author_email="kaanyzc2002@gmail.com",
    description="CLI example",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent"
    ],
    python_requires='>=3.8',
)