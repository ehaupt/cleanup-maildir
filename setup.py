#!/usr/bin/env python

from setuptools import setup

setup(
    name='cleanup-maildir',
    version='0.3.2',
    python_requires='>=3.6',
    install_requires=['pygraph @ git+https://github.com/jciskey/pygraph@master'],
    description='Script for cleaning up mails in Maildir folders based on arival date',
    author='Nathaniel W. Turner',
    author_email='nate@houseofnate.net',
    maintainer='Emanuel Haupt',
    maintainer_email='ehaupt@critical.ch',
    url='https://github.com/ehaupt/cleanup-maildir',
    scripts=['scripts/cleanup-maildir'],
)
